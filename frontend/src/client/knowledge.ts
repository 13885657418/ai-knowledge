import type { CancelablePromise } from "./core/CancelablePromise"
import { OpenAPI } from "./core/OpenAPI"
import { request as __request } from "./core/request"

export type KnowledgeBaseCreate = {
  name: string
  description?: string | null
}

export type KnowledgeBasePublic = {
  name: string
  description?: string | null
  id: string
  owner_id: string
  created_at?: string | null
}

export type KnowledgeBasesPublic = {
  data: Array<KnowledgeBasePublic>
  count: number
}

export type KnowledgeBaseUpdate = {
  name?: string | null
  description?: string | null
}

export type DocumentPublic = {
  file_name: string
  file_type: string
  content_type?: string | null
  file_size: number
  processing_status: string
  summary?: string | null
  id: string
  knowledge_base_id: string
  created_at?: string | null
  updated_at?: string | null
}

export type DocumentsPublic = {
  data: Array<DocumentPublic>
  count: number
}

export type KnowledgeBasesReadKnowledgeBasesData = {
  limit?: number
  skip?: number
}

export type KnowledgeBasesReadKnowledgeBaseData = {
  id: string
}

export type KnowledgeBasesCreateKnowledgeBaseData = {
  requestBody: KnowledgeBaseCreate
}

export type KnowledgeBasesUpdateKnowledgeBaseData = {
  id: string
  requestBody: KnowledgeBaseUpdate
}

export type KnowledgeBasesDeleteKnowledgeBaseData = {
  id: string
}

export type DocumentsReadDocumentsData = {
  knowledge_base_id?: string
  limit?: number
  skip?: number
}

export class KnowledgeBasesService {
  public static readKnowledgeBases(
    data: KnowledgeBasesReadKnowledgeBasesData = {},
  ): CancelablePromise<KnowledgeBasesPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/knowledge-bases/",
      query: {
        skip: data.skip,
        limit: data.limit,
      },
      errors: {
        422: "Validation Error",
      },
    })
  }

  public static readKnowledgeBase(
    data: KnowledgeBasesReadKnowledgeBaseData,
  ): CancelablePromise<KnowledgeBasePublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/knowledge-bases/{id}",
      path: {
        id: data.id,
      },
      errors: {
        404: "Not Found",
        422: "Validation Error",
      },
    })
  }

  public static createKnowledgeBase(
    data: KnowledgeBasesCreateKnowledgeBaseData,
  ): CancelablePromise<KnowledgeBasePublic> {
    return __request(OpenAPI, {
      method: "POST",
      url: "/api/v1/knowledge-bases/",
      body: data.requestBody,
      mediaType: "application/json",
      errors: {
        422: "Validation Error",
      },
    })
  }

  public static updateKnowledgeBase(
    data: KnowledgeBasesUpdateKnowledgeBaseData,
  ): CancelablePromise<KnowledgeBasePublic> {
    return __request(OpenAPI, {
      method: "PUT",
      url: "/api/v1/knowledge-bases/{id}",
      path: {
        id: data.id,
      },
      body: data.requestBody,
      mediaType: "application/json",
      errors: {
        404: "Not Found",
        422: "Validation Error",
      },
    })
  }

  public static deleteKnowledgeBase(
    data: KnowledgeBasesDeleteKnowledgeBaseData,
  ): CancelablePromise<{ message: string }> {
    return __request(OpenAPI, {
      method: "DELETE",
      url: "/api/v1/knowledge-bases/{id}",
      path: {
        id: data.id,
      },
      errors: {
        404: "Not Found",
        422: "Validation Error",
      },
    })
  }
}

export class DocumentsService {
  public static readDocuments(
    data: DocumentsReadDocumentsData = {},
  ): CancelablePromise<DocumentsPublic> {
    return __request(OpenAPI, {
      method: "GET",
      url: "/api/v1/documents/",
      query: {
        skip: data.skip,
        limit: data.limit,
        knowledge_base_id: data.knowledge_base_id,
      },
      errors: {
        422: "Validation Error",
      },
    })
  }
}
