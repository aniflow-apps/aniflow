"""Per-anime release profile management and deterministic test matching."""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.anime import Anime
from backend.app.models.release_profile import ReleaseProfile
from backend.app.schemas.release_profile import (
    FieldVerdictResponse,
    ParsedReleaseResponse,
    ProfileTestResponse,
    ReleaseProfileResponse,
    ReleaseProfileUpsert,
    TestReleaseRequest,
)
from backend.app.services.matching import evaluate_release
from backend.app.services.release_parser import parse_release_name

router = APIRouter()


def get_anime_or_404(anime_id: str, db: Session) -> Anime:
    anime = db.get(Anime, anime_id)
    if anime is None:
        raise HTTPException(status_code=404, detail="Anime not found")
    return anime


@router.get("/{anime_id}/profile", response_model=ReleaseProfileResponse | None)
def get_profile(anime_id: str, db: Session = Depends(get_db)) -> ReleaseProfileResponse | None:
    anime = get_anime_or_404(anime_id, db)
    if anime.profile is None:
        return None
    payload = ReleaseProfileResponse.model_validate(anime.profile)
    if anime.profile.template_id:
        from backend.app.models.profile_template import ProfileTemplate

        template = db.get(ProfileTemplate, anime.profile.template_id)
        payload.template_name = template.name if template else None
    return payload


@router.put("/{anime_id}/profile", response_model=ReleaseProfileResponse)
def put_profile(
    anime_id: str,
    request: ReleaseProfileUpsert,
    db: Session = Depends(get_db),
) -> ReleaseProfileResponse:
    anime = get_anime_or_404(anime_id, db)
    profile = anime.profile
    if profile is None:
        profile = ReleaseProfile(anime_id=anime.id)
        anime.profile = profile
    profile.subgroups = list(request.subgroups)
    profile.resolution = request.resolution
    profile.media_source = request.media_source
    profile.censor_preference = request.censor_preference
    profile.require_title_match = request.require_title_match
    profile.require_resolution_known = request.require_resolution_known
    profile.require_source_known = request.require_source_known
    profile.require_censor_known = request.require_censor_known
    db.commit()
    db.refresh(profile)
    return ReleaseProfileResponse.model_validate(profile)


@router.delete("/{anime_id}/profile", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(anime_id: str, db: Session = Depends(get_db)) -> Response:
    anime = get_anime_or_404(anime_id, db)
    if anime.profile is not None:
        db.delete(anime.profile)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{anime_id}/profile/test", response_model=ProfileTestResponse)
def test_release(
    anime_id: str,
    request: TestReleaseRequest,
    db: Session = Depends(get_db),
) -> ProfileTestResponse:
    anime = get_anime_or_404(anime_id, db)
    profile = anime.profile
    if profile is None:
        raise HTTPException(status_code=409, detail="A release profile must be saved before testing releases")
    try:
        parsed = parse_release_name(request.release_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    candidate_titles = [
        anime.display_name,
        anime.local_name,
        anime.title_english or "",
        anime.title_romaji or "",
        anime.title_native or "",
        *anime.aliases,
    ]
    result = evaluate_release(
        parsed,
        subgroups=list(profile.subgroups),
        resolution=profile.resolution,
        media_source=profile.media_source,
        edition=profile.edition or [],
        edition_not=profile.edition_not or [],
        censor_preference=profile.censor_preference.value,
        candidate_titles=candidate_titles,
        require_title_match=profile.require_title_match,
        require_resolution_known=profile.require_resolution_known,
        require_source_known=profile.require_source_known,
        require_censor_known=profile.require_censor_known,
    )
    return ProfileTestResponse(
        parsed=ParsedReleaseResponse(
            raw_name=parsed.raw_name,
            subgroup=parsed.subgroup,
            title=parsed.title,
            episode=parsed.episode,
            version=parsed.version,
            resolution=parsed.resolution,
            media_source=parsed.media_source,
            video_codec=parsed.video_codec,
            audio_codec=parsed.audio_codec,
            censored=parsed.censored,
            unknown_tags=list(parsed.unknown_tags),
        ),
        matched=result.matched,
        fields=[
            FieldVerdictResponse(field=item.field, verdict=item.verdict.value, expected=item.expected, actual=item.actual)
            for item in result.fields
        ],
        failures=result.failures,
    )
