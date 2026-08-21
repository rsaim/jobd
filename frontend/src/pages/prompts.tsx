/**
 * The saved-prompt library: labelled instruction text — tones, and anything
 * else worth writing once — that any "extra instructions" picker (today:
 * the reply box's generate/regenerate) can pull from instead of retyping
 * the same wording every time. Its own page rather than a modal off the
 * reply box, because a library is something you curate occasionally, not
 * something that belongs inline with a compose form.
 */

import { useState } from "react"
import { toast } from "sonner"
import { ScrollText, Trash2 } from "lucide-react"

import { useCreatePrompt, useDeletePrompt, usePrompts } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { EmptyState, ErrorState, Loading, PageHead, SectionHead } from "@/components/page"

export function PromptsPage() {
  const { data, isPending, error } = usePrompts()
  if (isPending) return <Loading />
  if (error) return <ErrorState error={error} />

  const { prompts } = data

  return (
    <div className="space-y-2">
      <PageHead
        icon={ScrollText}
        eyebrow="Reusable instructions"
        title="Prompts"
        lede="Saved tones and instructions any generate/regenerate button can pull from — write once, reuse everywhere."
      />

      <NewPromptForm />

      <SectionHead icon={ScrollText} count={prompts.length}>
        Saved
      </SectionHead>
      {prompts.length === 0 ? (
        <EmptyState title="Nothing saved yet" icon={ScrollText}>
          Add a tone or instruction above to make it pickable elsewhere.
        </EmptyState>
      ) : (
        <Card className="panel enter overflow-hidden rounded-xl py-0">
          {prompts.map((p) => (
            <PromptRow key={p.id} id={p.id} label={p.label} text={p.text} />
          ))}
        </Card>
      )}
    </div>
  )
}

function NewPromptForm() {
  const [label, setLabel] = useState("")
  const [text, setText] = useState("")
  const create = useCreatePrompt()

  const submit = () => {
    if (!label.trim() || !text.trim()) {
      toast.error("Needs both a label and text.")
      return
    }
    create.mutate(
      { label: label.trim(), text: text.trim() },
      {
        onSuccess: (r) => {
          if (r.ok) {
            toast.success("Saved.")
            setLabel("")
            setText("")
          } else {
            toast.error(r.message ?? "Could not save this.")
          }
        },
        onError: (e) => toast.error((e as Error).message),
      },
    )
  }

  return (
    <Card className="panel rounded-xl py-4">
      <CardContent className="flex flex-col gap-2 px-4">
        <div className="grid gap-2 sm:grid-cols-[14rem_1fr]">
          <div className="grid gap-1">
            <Label htmlFor="prompt-label" className="text-[11px]">
              Label
            </Label>
            <Input
              id="prompt-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Skeptical of low-ball offers"
              className="h-8 text-xs"
            />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="prompt-text" className="text-[11px]">
              Instruction text
            </Label>
            <Textarea
              id="prompt-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="What should the model do differently when this is picked?"
              rows={2}
              className="text-[13px]"
            />
          </div>
        </div>
        <Button size="sm" className="w-fit" disabled={create.isPending} onClick={submit}>
          {create.isPending ? "Saving…" : "Save prompt"}
        </Button>
      </CardContent>
    </Card>
  )
}

function PromptRow({ id, label, text }: { id: string; label: string; text: string }) {
  const del = useDeletePrompt()
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border/60 px-4 py-3 last:border-0">
      <div className="min-w-0">
        <p className="text-[13px] font-semibold">{label}</p>
        <p className="text-muted-foreground mt-0.5 text-[12.5px]">{text}</p>
      </div>
      <Button
        size="icon"
        variant="ghost"
        className="text-muted-foreground hover:text-destructive size-7 shrink-0"
        disabled={del.isPending}
        onClick={() => del.mutate(id, { onError: (e) => toast.error((e as Error).message) })}
        aria-label={`Delete ${label}`}
      >
        <Trash2 className="size-3.5" />
      </Button>
    </div>
  )
}
