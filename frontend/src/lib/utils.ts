import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** "openrouter/google/gemini-3.7-flash" -> "gemini-3.7-flash" — what a model
 *  picker shows where a full id wouldn't fit. The provider prefix is identical
 *  across the allowlist and only the tail tells models apart, which is exactly
 *  the part mid-string truncation would eat. Full ids stay in dropdown lists
 *  and in `title` hovers. */
export function shortModelName(id: string): string {
  return id.split("/").pop() || id
}
