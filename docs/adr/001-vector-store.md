# ADR-001 — Vector store para RAG

**Estado:** Aceptada
**Fecha:** 2026-05-10
**Sprint:** 00 Foundation

## Contexto

Necesitamos un vector store para indexar embeddings de actas y soportar
"Ask Notiva" (Sprint 02). Volúmenes esperados: 1k–100k chunks en el
primer año, ~$0.0002 por sesión en costo de embeddings.

## Decisión

**pgvector en Supabase Postgres 17**.

- Ya tenemos Postgres managed (sin nuevo proveedor).
- pgvector 0.8.0 instalado y verificado contra `secretaria-ai-platform`.
- Índice `IVFFlat (lists=100, vector_cosine_ops)` es suficiente hasta ~100k.
- Costo: $0 incremental.

## Alternativas descartadas

| Opción | Motivo de rechazo |
|---|---|
| Pinecone managed | +$70/mes mínimo, latencia red extra, sin upside hasta >1M chunks |
| Weaviate self-hosted | Otro contenedor para administrar, sin valor incremental |
| Qdrant Cloud | Mejor producto técnico, pero proveedor extra |

## Consecuencias

- Migración a Pinecone si superamos 1M chunks (recreate index).
- Backup de embeddings va junto al backup de Supabase (sin trabajo adicional).
