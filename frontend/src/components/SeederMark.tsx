import { t } from "../i18n";
import { seedState } from "../seeders";

/** A dot and a number: green with seeders, red with none, grey when the tracker said nothing (build 247). */
export function SeederMark({ seeders }: { seeders: number | null | undefined }) {
  const state = seedState(seeders);
  const title = state === "unknown" ? t("seed.unknown") : state === "dead" ? t("seed.dead") : t("seed.alive").replace("{n}", String(seeders));
  return (
    <span className={`seed-mark seed-${state}`} title={title} aria-label={title}>
      {state === "unknown" ? "?" : seeders}
    </span>
  );
}
