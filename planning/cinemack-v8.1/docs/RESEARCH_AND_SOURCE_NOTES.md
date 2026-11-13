# Research references, evidence boundaries and design notes

**Source distinctions:** The current system facts below come from the user-supplied Nov 11 2026 README. External references are general best-practice guidance, not proof that a proposed technique will improve Cinemack. The master prompt's metrics and feature gates are hypotheses until measured against the actual repo.

## Directly from README (Nov 11, round 27)

- `retrieve.py`, `movies.db`, `wiki.db`: movie/Wikipedia indexes; `tools.py`, `facts.db`: SQL facts; Ollama `movie-buff` planner/answer; FastAPI + no-build web v8.
- Live v7 `SEMCACHE`; off/pending switches include `NEWS`, `PERF`, `MEM_CARDS`, `CLAIM_REWRITE`, `CTX_BUILDER_V=3`, `GUARD2`, `PERSONA` and shadow fine-tuned embedder.
- Box-office target log(gross/budget), pf4 MAE 1.200 on stated holdout, pf5 A/C/D results not improving, B Guardian collection pending, reference 80% interval coverage 78.1%.
- Existing calendar includes TVmaze localized air times; homepage genre Any/All and Pick Tonight hard limits already exist; keep prior approved site behavior.
- Site remains non-commercial student research; licensing/attribution matters.

## Sources supplied by user

- IBM RAG answer generation: https://www.ibm.com/think/architectures/rag-cookbook/answer-generation
- ML Journey large dataset indexing: https://mljourney.com/indexing-large-text-datasets-for-rag-best-practices/
- Google Cloud RAG retrieval evaluation: https://cloud.google.com/blog/products/ai-machine-learning/optimizing-rag-retrieval
- Redis RAG techniques: https://redis.io/blog/10-techniques-to-improve-rag-accuracy/
- Hugging Face semantic caching: https://huggingface.co/learn/cookbook/en/semantic_cache_chroma_vector_database
- Databricks recommendations: https://www.databricks.com/blog/guide-to-building-online-recommendation-system
- FeaturAsk suggestion feedback: https://featurask.com/blog/collect-use-user-suggestions
- Tezeract recommendation system: https://tezeract.ai/build-a-recommendation-system/
- Cake predictive model guide: https://www.cake.ai/blog/predictive-model-open-source
- ClickIT predictive model guide: https://www.clickittech.com/ai/how-to-build-a-predictive-ai-model/
- Ollama tool calling: https://docs.ollama.com/capabilities/tool-calling
- IBM local tool calling: https://www.ibm.com/think/tutorials/local-tool-calling-ollama-granite
- GIGAGPU multiple models: https://gigagpu.com/run-multiple-ai-models-single-gpu/
- SitePoint shared GPU: https://www.sitepoint.com/team-shared-local-ai-gpu-multiple-developers/

## Additional official references checked November 12, 2026

- Ollama structured outputs JSON schema/Pydantic: https://docs.ollama.com/capabilities/structured-outputs
- Ollama tool calling with function arguments: https://docs.ollama.com/capabilities/tool-calling
- Ollama local FAQ serving/context configuration: https://docs.ollama.com/faq
- OWASP GenAI LLM 2026 Top 10 (including prompt injection, excessive agency, vector/embedding weaknesses): https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/blob/main/2026/README.md
- OWASP prompt injection prevention: https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
- web.dev Web Vitals thresholds: https://web.dev/articles/vitals
- TMDB API attribution and non-commercial terms: https://developer.themoviedb.org/docs/faq
- WCAG 2.2 recommendation: https://www.w3.org/TR/WCAG22/
- SQLite WAL behavior and backup procedures: https://www.sqlite.org/wal.html and https://www.sqlite.org/backup.html

## Model validation and scientific caution

- Each experiment must separate historical snapshot availability from current crawled values; post-release review counts, final cast credits, news, revenue and popularity cannot leak into a pre-release forecast.
- Correlations of release timing/cast/director/writer/producer/genre with grosses may be confounded by production budgets, distribution scale, franchise status, historical era, global shocks and data selection. Association ≠ causation.
- Claiming a real confidence/interval percentage requires testing a defined forecast target, data split and calibration sample, not just outputting lower and upper numbers.
- A predicted gross/budget multiple is not a studio net-return/ROI estimate. Production budget alone excludes key expense/revenue components.
- Reporting actual prices for theater deals requires source-verified location-specific pricing, eligibility, taxes and fees. A published discount program is not proof of an exact ticket quote.

## Verification checklist for added sources

Before incorporation, establish license/rights, provider ID mapping, API quota, effective/published timestamp, last verified timestamp, geographies, attributable text length, storage permissions, refresh policy and what constitutes "unknown." Never substitute current page data for historical as-of states.
