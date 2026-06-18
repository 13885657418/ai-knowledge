import { EllipsisVertical } from "lucide-react"
import { useState } from "react"

import type { KnowledgeBasePublic } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import DeleteKnowledgeBase from "./DeleteKnowledgeBase"
import EditKnowledgeBase from "./EditKnowledgeBase"

interface KnowledgeBaseActionsMenuProps {
  knowledgeBase: KnowledgeBasePublic
}

export const KnowledgeBaseActionsMenu = ({
  knowledgeBase,
}: KnowledgeBaseActionsMenuProps) => {
  const [open, setOpen] = useState(false)

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon">
          <EllipsisVertical />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <EditKnowledgeBase
          knowledgeBase={knowledgeBase}
          onSuccess={() => setOpen(false)}
        />
        <DeleteKnowledgeBase
          id={knowledgeBase.id}
          onSuccess={() => setOpen(false)}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
