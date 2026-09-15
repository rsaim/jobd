/** The "who am I" card, and the way out of the demo.
 *
 *  Only exists behind the public demo's gate: that cookie is how the demo
 *  logs a visitor in, so its presence is the one honest signal that "sign
 *  out" means something here. A real deployment has no cookie and renders
 *  nothing — the app itself has no accounts to sign out of.
 */
import { LogOut } from "lucide-react"

import { useChrome } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

const GATE_COOKIE = "jobd_demo="

export function UserMenu() {
  const { data } = useChrome()
  if (!document.cookie.includes(GATE_COOKIE)) return null

  const account = data?.account ?? "demo"
  const initial = account.charAt(0).toUpperCase()
  const signOut = () => {
    document.cookie = "jobd_demo=; Path=/; Max-Age=0"
    window.location.assign("/")
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Signed in to the demo">
          <Avatar className="size-6">
            <AvatarFallback className="bg-primary text-primary-foreground text-[0.7rem] font-semibold">
              {initial}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <div className="flex flex-col items-center px-4 py-4 text-center">
          <Avatar className="size-14">
            <AvatarFallback className="bg-primary text-primary-foreground text-xl font-semibold">
              {initial}
            </AvatarFallback>
          </Avatar>
          <div className="mt-2 text-sm font-medium">Demo user</div>
          <div className="text-muted-foreground text-xs">{account}</div>
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={signOut}>
          <LogOut /> Sign out of the demo
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
