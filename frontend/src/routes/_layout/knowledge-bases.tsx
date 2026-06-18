import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Outlet, useMatch } from "@tanstack/react-router"
import { BookOpen } from "lucide-react"
import { Suspense } from "react"

import { KnowledgeBasesService } from "@/client"
import { DataTable } from "@/components/Common/DataTable"
import AddKnowledgeBase from "@/components/KnowledgeBases/AddKnowledgeBase"
import { columns } from "@/components/KnowledgeBases/columns"
import PendingKnowledgeBases from "@/components/Pending/PendingKnowledgeBases"

function getKnowledgeBasesQueryOptions() {
  return {
    queryFn: () => KnowledgeBasesService.readKnowledgeBases({ skip: 0, limit: 100 }),
    queryKey: ["knowledge-bases"],
  }
}

export const Route = createFileRoute("/_layout/knowledge-bases")({
  component: KnowledgeBases,
  head: () => ({
    meta: [
      {
        title: "Knowledge Bases - FastAPI Template",
      },
    ],
  }),
})

function KnowledgeBasesTableContent() {
  const { data: knowledgeBases } = useSuspenseQuery(getKnowledgeBasesQueryOptions())

  if (knowledgeBases.data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center py-12">
        <div className="rounded-full bg-muted p-4 mb-4">
          <BookOpen className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold">
          You don't have any knowledge bases yet
        </h3>
        <p className="text-muted-foreground">
          Add a new knowledge base to get started
        </p>
      </div>
    )
  }

  return <DataTable columns={columns} data={knowledgeBases.data} />
}

function KnowledgeBasesTable() {
  return (
    <Suspense fallback={<PendingKnowledgeBases />}>
      <KnowledgeBasesTableContent />
    </Suspense>
  )
}

function KnowledgeBasesList() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Knowledge Bases</h1>
          <p className="text-muted-foreground">
            Create and manage your knowledge bases
          </p>
        </div>
        <AddKnowledgeBase />
      </div>
      <KnowledgeBasesTable />
    </div>
  )
}

function KnowledgeBases() {
  const childMatch = useMatch({
    from: "/_layout/knowledge-bases/$knowledgeBaseId/documents",
    shouldThrow: false,
  })

  if (childMatch) {
    return <Outlet />
  }

  return <KnowledgeBasesList />
}
