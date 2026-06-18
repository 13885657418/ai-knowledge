import type { ColumnDef } from "@tanstack/react-table"
import { Link } from "@tanstack/react-router"
import type { KnowledgeBasePublic } from "@/client"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { KnowledgeBaseActionsMenu } from "./KnowledgeBaseActionsMenu"

export const columns: ColumnDef<KnowledgeBasePublic>[] = [
  {
    accessorKey: "id",
    header: "ID",
    cell: ({ row }) => (
      <span className="font-mono text-xs text-muted-foreground select-all">
        {row.original.id}
      </span>
    ),
  },
  {
    accessorKey: "name",
    header: "Name",
    cell: ({ row }) => (
      <Link
        to="/knowledge-bases/$knowledgeBaseId/documents"
        params={{ knowledgeBaseId: row.original.id }}
        className="font-medium hover:underline"
      >
        {row.original.name}
      </Link>
    ),
  },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) => {
      const description = row.original.description
      return (
        <span
          className={cn(
            "max-w-xs truncate block text-muted-foreground",
            !description && "italic",
          )}
        >
          {description || "No description"}
        </span>
      )
    },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => {
      const createdAt = row.original.created_at
      return (
        <span className="text-muted-foreground">
          {createdAt ? new Date(createdAt).toLocaleString() : "N/A"}
        </span>
      )
    },
  },
  {
    id: "documents",
    header: "Documents",
    cell: ({ row }) => (
      <Button asChild variant="outline" size="sm">
        <Link
          to="/knowledge-bases/$knowledgeBaseId/documents"
          params={{ knowledgeBaseId: row.original.id }}
        >
          View documents
        </Link>
      </Button>
    ),
  },
  {
    id: "actions",
    header: () => <span className="sr-only">Actions</span>,
    cell: ({ row }) => (
      <div className="flex justify-end">
        <KnowledgeBaseActionsMenu knowledgeBase={row.original} />
      </div>
    ),
  },
]
