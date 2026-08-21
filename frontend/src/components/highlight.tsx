/**
 * Marking the words someone typed, wherever they land.
 *
 * Two surfaces need this and they need it to look the same: semantic search
 * marks the query words that actually occur in a hit (the shared-words claim,
 * made visible instead of asserted), and Home's filter marks why a card
 * survived the filter. A reader who learns the yellow-ish wash on one page
 * should not have to learn it again on the other.
 */

import type { ReactNode } from "react"

const escape = (word: string) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")

/** A typed box into words. Empty in, empty out — callers treat that as
 *  "no filter" rather than "matches nothing". */
export function terms(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean)
}

/** Every word must appear somewhere in the haystack. AND, not OR: typing a
 *  second word is how you narrow a list, and an OR would widen it. */
export function matches(haystack: string, words: string[]): boolean {
  if (!words.length) return true
  const hay = haystack.toLowerCase()
  return words.every((word) => hay.includes(word))
}

export function Marked({
  text,
  words,
}: {
  text: string
  words: string[]
}): ReactNode {
  if (!text) return null
  if (!words.length) return text
  const pattern = new RegExp(`(${words.map(escape).join("|")})`, "gi")
  // A single capturing group means split() interleaves the matches at the
  // odd indices — no manual scan, no lost characters.
  return text.split(pattern).map((piece, index) =>
    index % 2 === 1 ? (
      <mark
        key={index}
        className="bg-primary/15 text-foreground rounded-[3px] px-0.5 py-px"
      >
        {piece}
      </mark>
    ) : (
      piece
    ),
  )
}
