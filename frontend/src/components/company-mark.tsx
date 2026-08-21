/**
 * A company's face: its own mark where one was cached, its initial otherwise.
 *
 * The initial is not a placeholder waiting to be replaced — it is the answer
 * for the ~40% of this record that is a recruiting agency with no website, a
 * company discovered from a signature block, or a domain neither favicon
 * proxy has ever seen. Both states are drawn on the same plate at the same
 * size, so a list of fifty companies stays a column of even shapes rather
 * than a ransom note.
 *
 * The bytes come from this machine (`/api/company/<id>/logo`, cached in
 * Postgres by `jobd logos`). No page here ever asks a logo CDN for anything;
 * see services/logos.py for why that distinction is the whole design.
 *
 * A logo the browser cannot decode falls back to the initial rather than to a
 * broken-image glyph: the record has ICO, PNG and WEBP in it, and a mark that
 * fails is worse than one that was never there.
 */

import { useState } from "react"

import { useLogos } from "@/lib/api"
import { cn } from "@/lib/utils"

const SIZES = {
  sm: "size-5 rounded text-[10px]",
  md: "size-7 rounded-md text-[12px]",
  lg: "size-10 rounded-lg text-[16px]",
} as const

export function CompanyMark({
  id,
  name,
  size = "sm",
  className,
}: {
  id: string
  name: string
  size?: keyof typeof SIZES
  className?: string
}) {
  const { data } = useLogos()
  const [broken, setBroken] = useState(false)
  const show = Boolean(id) && !broken && data?.has.has(id)

  return (
    <span
      aria-hidden
      className={cn(
        "ring-border grid shrink-0 place-items-center overflow-hidden font-mono font-semibold ring-1",
        SIZES[size],
        // A favicon is usually drawn for a light browser tab, so a dark mark
        // on a dark surface would vanish. The plate stays light in both
        // themes for that reason — it is the tab the logo expects — while
        // the fallback keeps the chrome's own muted plate.
        show ? "bg-white" : "bg-muted text-muted-foreground",
        className,
      )}
    >
      {show ? (
        <img
          src={`/api/company/${id}/logo`}
          alt=""
          loading="lazy"
          decoding="async"
          className="size-full object-contain p-0.5"
          onError={() => setBroken(true)}
        />
      ) : (
        (name.trim()[0] ?? "?").toUpperCase()
      )}
    </span>
  )
}
