"""Reusable release-profile templates."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func as sqlfunc
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.profile_template import ProfileTemplate
from backend.app.services.config_sync import SUBGROUPS, forget_file, sync_from_files, write_back
from backend.app.models.release_profile import CensorPreference, ReleaseProfile
from backend.app.schemas.profile_template import (
    ApplyTemplateRequest,
    ApplyTemplateResponse,
    FeedReadResult,
    GroupSightingResult,
    ProfileTemplateResponse,
    ProfileTemplateUpsert,
    ProfileTestResponse,
    ProfileTestResultResponse,
)
from backend.app.services import profile_test

router = APIRouter()


def _apply_values(profile: ReleaseProfile, template: ProfileTemplate) -> None:
    profile.subgroups = list(template.subgroups)
    profile.resolution = template.resolution
    profile.media_source = template.media_source
    profile.edition = list(template.edition or [])
    profile.edition_not = list(template.edition_not or [])
    profile.censor_preference = template.censor_preference
    profile.require_title_match = template.require_title_match
    profile.require_resolution_known = template.require_resolution_known
    profile.require_source_known = template.require_source_known
    profile.require_censor_known = template.require_censor_known
    profile.template_id = template.id


def _clear_other_defaults(db: Session, keep_id: str | None) -> None:
    query = select(ProfileTemplate).where(ProfileTemplate.is_default.is_(True))
    if keep_id is not None:
        query = query.where(ProfileTemplate.id != keep_id)
    for other in db.scalars(query):
        other.is_default = False


def _ensure_single_default(db: Session) -> None:
    """With exactly one profile it is always the default."""
    templates = list(db.scalars(select(ProfileTemplate)))
    if len(templates) == 1:
        templates[0].is_default = True


@router.post("/test", response_model=ProfileTestResponse)
def test_all_templates(db: Session = Depends(get_db)) -> ProfileTestResponse:
    """Every profile against one read of the feeds.

    One read, not one per profile: ten profiles must not be ten feed fetches
    of the same few hundred releases. See services/profile_test.py.
    """
    return _report(profile_test.test_profiles(db))


@router.post("/{template_id}/test", response_model=ProfileTestResponse)
def test_one_template(template_id: str, db: Session = Depends(get_db)) -> ProfileTestResponse:
    """One profile: does it match anything the feeds are carrying?"""
    if db.get(ProfileTemplate, template_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _report(profile_test.test_profiles(db, template_id=template_id))


def _report(report: profile_test.ProfileTestReport) -> ProfileTestResponse:
    return ProfileTestResponse(
        feeds=[
            FeedReadResult(name=feed.name, items=feed.items, error=feed.error)
            for feed in report.feeds
        ],
        results=[
            ProfileTestResultResponse(
                template_id=result.template_id,
                name=result.name,
                verdict=result.verdict,
                accepted=result.accepted,
                considered=result.considered,
                groups=[
                    GroupSightingResult(
                        name=sighting.name,
                        items=sighting.items,
                        resolutions=list(sighting.resolutions),
                        example=sighting.example,
                    )
                    for sighting in result.groups
                ],
                examples=list(result.examples),
                top_failures=list(result.top_failures),
            )
            for result in report.results
        ],
        items=report.items,
        reached_any_feed=report.reached_any_feed,
    )


@router.get("", response_model=list[ProfileTemplateResponse])
def list_templates(db: Session = Depends(get_db)) -> list[ProfileTemplateResponse]:
    # A file dropped into the subgroup folder is meant to appear here.
    sync_from_files(db)
    usage = dict(
        db.execute(
            select(ReleaseProfile.template_id, sqlfunc.count(ReleaseProfile.id))
            .where(ReleaseProfile.template_id.is_not(None))
            .group_by(ReleaseProfile.template_id)
        ).all()
    )
    results: list[ProfileTemplateResponse] = []
    order = (ProfileTemplate.is_default.desc(), ProfileTemplate.name.asc())
    for template in db.scalars(select(ProfileTemplate).order_by(*order)):
        payload = ProfileTemplateResponse.model_validate(template)
        payload.anime_count = usage.get(template.id, 0)
        results.append(payload)
    return results


@router.post("", response_model=ProfileTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(request: ProfileTemplateUpsert, db: Session = Depends(get_db)) -> ProfileTemplate:
    is_default = request.is_default or db.scalar(select(sqlfunc.count(ProfileTemplate.id))) == 0
    if is_default:
        _clear_other_defaults(db, keep_id=None)
    template = ProfileTemplate(
        name=request.name,
        subgroups=list(request.subgroups),
        resolution=request.resolution,
        media_source=request.media_source,
        censor_preference=CensorPreference(request.censor_preference),
        require_title_match=request.require_title_match,
        require_resolution_known=request.require_resolution_known,
        require_source_known=request.require_source_known,
        require_censor_known=request.require_censor_known,
        is_default=is_default,
    )
    db.add(template)
    try:
        db.flush()
        write_back(db, SUBGROUPS, ProfileTemplate)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A template with this name already exists") from exc
    db.refresh(template)
    return template


@router.put("/{template_id}", response_model=ProfileTemplateResponse)
def update_template(
    template_id: str,
    request: ProfileTemplateUpsert,
    db: Session = Depends(get_db),
) -> ProfileTemplate:
    template = db.get(ProfileTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if request.is_default:
        _clear_other_defaults(db, keep_id=template_id)
    template.name = request.name
    template.subgroups = list(request.subgroups)
    template.resolution = request.resolution
    template.media_source = request.media_source
    template.censor_preference = CensorPreference(request.censor_preference)
    template.require_title_match = request.require_title_match
    template.require_resolution_known = request.require_resolution_known
    template.require_source_known = request.require_source_known
    template.require_censor_known = request.require_censor_known
    template.is_default = request.is_default
    _ensure_single_default(db)
    try:
        db.flush()
        write_back(db, SUBGROUPS, ProfileTemplate)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A template with this name already exists") from exc
    db.refresh(template)
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, db: Session = Depends(get_db)) -> Response:
    template = db.get(ProfileTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    forget_file(SUBGROUPS, template)
    # Anime profiles keep their copied values; only the link disappears.
    db.delete(template)
    _ensure_single_default(db)
    # Deleting the default promotes another one, and that promotion has to
    # reach its file too - otherwise the next read puts the old default back.
    db.flush()
    write_back(db, SUBGROUPS, ProfileTemplate)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/apply/{anime_id}", response_model=ApplyTemplateResponse)
def apply_template_to_anime(anime_id: str, request: ApplyTemplateRequest, db: Session = Depends(get_db)):
    """Copy a template's values into the anime's profile (or detach)."""
    from backend.app.api.routes.release_profiles import get_anime_or_404

    anime = get_anime_or_404(anime_id, db)
    profile = anime.profile

    if request.template_id is None:
        if profile is not None:
            profile.template_id = None
            db.commit()
        return ApplyTemplateResponse(applied=False, template_name=None)

    template = db.get(ProfileTemplate, request.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if profile is None:
        profile = ReleaseProfile(anime_id=anime.id)
        anime.profile = profile
    _apply_values(profile, template)
    db.commit()
    return ApplyTemplateResponse(applied=True, template_name=template.name)
