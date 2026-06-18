import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { FileText } from "lucide-react"
import { Suspense } from "react"

import { DocumentsService, KnowledgeBasesService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import AddDocument from "@/components/Documents/AddDocument"
import { columns } from "@/components/Documents/columns"
import PendingDocuments from "@/components/Pending/PendingDocuments"

function getKnowledgeBaseQueryOptions(knowledgeBaseId: string) {
  return {
    queryFn: () => KnowledgeBasesService.readKnowledgeBase({ id: knowledgeBaseId }),
    queryKey: ["knowledge-base", knowledgeBaseId],
  }
}

function getDocumentsQueryOptions(knowledgeBaseId: string) {
  return {
    queryFn: () =>
      DocumentsService.readDocuments({
        knowledge_base_id: knowledgeBaseId,
        skip: 0,
        limit: 100,
      }),
    queryKey: ["knowledge-base-documents", knowledgeBaseId],
  }
}

export const Route = createFileRoute(
  "/_layout/knowledge-bases/$knowledgeBaseId/documents",
)({
  component: KnowledgeBaseDocuments,
  head: () => ({
    meta: [
      {
        title: "Documents - FastAPI Template",
      },
    ],
  }),
})

function DocumentsTableContent() {
  const { knowledgeBaseId } = Route.useParams()
  const { data: documents } = useSuspenseQuery(
    getDocumentsQueryOptions(knowledgeBaseId),
  )

  if (documents.data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <FileText className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">No documents yet</h3>
        <p className="text-muted-foreground">
          This knowledge base does not contain any documents right now.
        </p>
      </div>
    )
  }

  return <DataTable columns={columns} data={documents.data} />
}

function DocumentsTable() {
  return (
    <Suspense fallback={<PendingDocuments />}>
      <DocumentsTableContent />
    </Suspense>
  )
}

function KnowledgeBaseDocuments() {
  const { knowledgeBaseId } = Route.useParams()
  const { data: knowledgeBase } = useSuspenseQuery(
    getKnowledgeBaseQueryOptions(knowledgeBaseId),
  )

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{knowledgeBase.name}</h1>
          <p className="text-muted-foreground">
            Browse documents stored in this knowledge base
          </p>
        </div>
        <AddDocument knowledgeBaseId={knowledgeBaseId} />
      </div>
      <DocumentsTable />
    </div>
  )
}
