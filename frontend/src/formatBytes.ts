/**
 * A size in bytes as a person reads it (build 233): "850 MB", "1.2 TB". Binary steps, like the checks' own
 * "GB", and one decimal from a gigabyte up, where a decimal is worth reading.
 */
const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "?";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit >= 3 && value < 100 ? 1 : 0;
  return `${value.toFixed(digits)} ${UNITS[unit]}`;
}

/** Less than this free is worth a colour (the same floor the Checks page warns at). */
export const LOW_SPACE_BYTES = 5 * 1024 ** 3;
