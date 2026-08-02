import type { MeasurementQuality } from "../types/report";

export function shouldShowInterpretiveResults(
  status: MeasurementQuality["status"] | null | undefined,
): boolean {
  return status !== "invalid";
}
