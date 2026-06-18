import type { ColumnDef } from "@tanstack/react-table"

import type { DocumentPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import DeleteDocument from "./DeleteDocument"

export const columns: ColumnDef<DocumentPublic>[] = [
  {
    accessorKey: "id",
    header: "Document ID",
    cell: ({ row }) => (
      <span className="font-mono text-xs text-muted-foreground select-all">
        {row.original.id}
      </span>
    ),
  },
  {
    accessorKey: "file_name",
    header: "File Name",
    cell: ({ row }) => (
      <span className="font-medium">{row.original.file_name}</span>
    ),
  },
  {
    accessorKey: "file_type",
    header: "Type",
    cell: ({ row }) => (
      <Badge variant="outline">{row.original.file_type}</Badge>
    ),
  },
  {
    accessorKey: "file_size",
    header: "Size",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {row.original.file_size} bytes
      </span>
    ),
  },
  {
    accessorKey: "processing_status",
    header: "Status",
    cell: ({ row }) => {
      const status = row.original.processing_status
      const isPending = status === "pending"
      return (
        <span className={cn(!isPending && "text-muted-foreground")}>
          {status}
        </span>
      )
    },
  },
  {
    accessorKey: "summary",
    header: "Summary",
    cell: ({ row }) => {
      const summary = row.original.summary
      return (
        <span
          className={cn(
            "max-w-xs truncate block text-muted-foreground",
            !summary && "italic",
          )}
        >
          {summary || "No summary"}
        </span>
      )
    },
  },
  {
    id: "actions",
    header: "",
    cell: ({ row }) => (
      <div className="flex justify-end">
        <DeleteDocument
          id={row.original.id}
          knowledgeBaseId={row.original.knowledge_base_id}
          fileName={row.original.file_name}
        />
      </div>
    ),
  },
]
