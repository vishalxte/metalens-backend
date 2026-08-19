# ITC 2025 MATRIZ Proceedings — Index & MetaLens Hooks (retrieval-first)

_Generated: 2026-01-05 (UTC). Source PDF: `Proceedings-ITC-2025-MATRIZ-Official-compressed.pdf`_

## What this file is for
- Make the 2025 proceedings **easy to retrieve** (high signal, low noise).
- Provide **safe entry points** for strict mode: where to look, what to search, and what to extract.
- Convert paper themes into **MetaLens-compatible hooks** (contradictions/resources/tests) without claiming more than the text supports.

## How to use in retrieval
When you need ITC2025 support, query **this index first**, then jump to the PDF page(s).

Suggested query patterns:
- `ITC2025 <topic> index` (e.g., `ITC2025 contradiction index`)
- `ITC2025 paper_id <ID>` (e.g., `ITC2025_P176`)
- `ITC2025 <token>` using the **retrieval tokens** listed per entry

## Page mapping note
For the indexed papers below, the PDF page number is consistently **printed_page + 7** (verified at multiple points, including printed pages 43→PDF50, 176→PDF183, 260→PDF267).
If your PDF viewer shows different numbering, prefer the **PDF page** given here.

## Quick index table
| Paper ID | Title | Proceedings page | PDF page | Retrieval tokens |
|---|---|---:|---:|---|
| ITC2025_P043_TRIZ_LLMs_Hybrid_Patent | TRIZ + LLMs hybrid model for patent innovation | 43 | 50 | triz, llm, hybrid, patent, innovation |
| ITC2025_P057_GPT_Contradiction_Analyzer | Automated contradiction analysis in patents using a GPT-based analyzer | 57 | 64 | contradiction, analysis, patent, gpt, analyzer |
| ITC2025_P100_Stories_ChatGPT_TRIZ | A few stories about ChatGPT and TRIZ | 100 | 107 | chatgpt, triz, stories |
| ITC2025_P114_TRIZ_Chatbot_Investing | Development & case study of a TRIZ-based chatbot for investing | 114 | 121 | triz, chatbot, invest |
| ITC2025_P129_Education_TRIZ_AI | Horizons of education: integration of TRIZ and AI | 129 | 136 | education, triz, ai |
| ITC2025_P144_Services_AI_Era | TRIZ for services in the AI era | 144 | 151 | services, triz, ai |
| ITC2025_P176_FOS_GenAI_DataProtection | Function Oriented Search in Generative AI under data protection constraints | 176 | 183 | functionorientedsearch, generativeai, dataprotection |
| ITC2025_P260_TRIZ_Systematic_Presentation | TRIZ ideas in a systematic presentation: application options for innovation | 260 | 267 | systematic, presentation, triz, innovation |

## Detailed entries (MetaLens integration hooks)

### ITC2025_P043_TRIZ_LLMs_Hybrid_Patent: TRIZ + LLMs hybrid model for patent innovation

- Location: proceedings p43 (PDF p50)
- Retrieval tokens: triz, llm, hybrid, patent, innovation
- Observed terms in first pages: method, patent, contradiction, llm, generativeai

**When to retrieve this**
- You’re analyzing patents (or prior art) and need structured contradiction extraction or concept generation.
- You’re designing prompts/pipelines that combine TRIZ reasoning with LLM output constraints.

**Possible MetaLens hooks (safe, non-claiming)**
- Add a ‘contradiction extraction micro-checklist’ before proposing solutions (forces explicit tradeoff statement).
- Add a ‘contradiction normalization’ step: express as *improve X worsens Y* with context/time horizon.
- Use a prompt template that separates: facts → assumptions → unknowns → contradiction → resources → tests.
- Require a disconfirming test and a REF_NOT_FOUND fallback for unverifiable citations.
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

### ITC2025_P057_GPT_Contradiction_Analyzer: Automated contradiction analysis in patents using a GPT-based analyzer

- Location: proceedings p57 (PDF p64)
- Retrieval tokens: contradiction, analysis, patent, gpt, analyzer
- Observed terms in first pages: prompt, pipeline, evaluation, framework, method, case, patent, contradiction, llm, gpt

**When to retrieve this**
- You’re analyzing patents (or prior art) and need structured contradiction extraction or concept generation.
- You need an automated contradiction formulation/checklist to reduce missed contradictions.
- You’re designing prompts/pipelines that combine TRIZ reasoning with LLM output constraints.

**Possible MetaLens hooks (safe, non-claiming)**
- Add a ‘contradiction extraction micro-checklist’ before proposing solutions (forces explicit tradeoff statement).
- Add a ‘contradiction normalization’ step: express as *improve X worsens Y* with context/time horizon.
- Use a prompt template that separates: facts → assumptions → unknowns → contradiction → resources → tests.
- Require a disconfirming test and a REF_NOT_FOUND fallback for unverifiable citations.
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

### ITC2025_P100_Stories_ChatGPT_TRIZ: A few stories about ChatGPT and TRIZ

- Location: proceedings p100 (PDF p107)
- Retrieval tokens: chatgpt, triz, stories
- Observed terms in first pages: prompt, method, case, example, patent, contradiction, education, services, llm, gpt

**When to retrieve this**
- You’re designing prompts/pipelines that combine TRIZ reasoning with LLM output constraints.

**Possible MetaLens hooks (safe, non-claiming)**
- Use a prompt template that separates: facts → assumptions → unknowns → contradiction → resources → tests.
- Require a disconfirming test and a REF_NOT_FOUND fallback for unverifiable citations.
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

### ITC2025_P114_TRIZ_Chatbot_Investing: Development & case study of a TRIZ-based chatbot for investing

- Location: proceedings p114 (PDF p121)
- Retrieval tokens: triz, chatbot, invest
- Observed terms in first pages: prompt, framework, method, case, patent, contradiction, chatbot, llm, gpt, generativeai

**When to retrieve this**
- You’re building a domain chatbot and need method discipline + evaluation hooks.

**Possible MetaLens hooks (safe, non-claiming)**
- Define domain guardrails + evaluation: what outputs count as correct (AC), and what errors are unacceptable.
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

### ITC2025_P129_Education_TRIZ_AI: Horizons of education: integration of TRIZ and AI

- Location: proceedings p129 (PDF p136)
- Retrieval tokens: education, triz, ai
- Observed terms in first pages: prompt, evaluation, framework, method, education

**When to retrieve this**
- You’re designing training/teaching flows for TRIZ+AI or onboarding users into structured thinking.

**Possible MetaLens hooks (safe, non-claiming)**
- Provide a ‘fast lane vs deep lane’ teaching progression with explicit mastery checks (AC).
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

### ITC2025_P144_Services_AI_Era: TRIZ for services in the AI era

- Location: proceedings p144 (PDF p151)
- Retrieval tokens: services, triz, ai
- Observed terms in first pages: evaluation, framework, method, case, example, contradiction, chatbot, education, services, gpt

**When to retrieve this**
- You’re applying TRIZ to services/processes (not physical products) and need appropriate framing.

**Possible MetaLens hooks (safe, non-claiming)**
- Prefer process/function analysis language (flows, actors, constraints) before jumping to physical analogies.
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

### ITC2025_P176_FOS_GenAI_DataProtection: Function Oriented Search in Generative AI under data protection constraints

- Location: proceedings p176 (PDF p183)
- Retrieval tokens: functionorientedsearch, generativeai, dataprotection
- Observed terms in first pages: prompt, method, case, dataprotection, privacy, education, gpt, generativeai

**When to retrieve this**
- You need function-oriented retrieval with privacy/data protection constraints.

**Possible MetaLens hooks (safe, non-claiming)**
- Add a ‘data protection constraint’ gate: what data cannot be used, and what proxies/resources exist.
- Use function-oriented search queries (verb+noun) and sanitize sensitive terms.
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

### ITC2025_P260_TRIZ_Systematic_Presentation: TRIZ ideas in a systematic presentation: application options for innovation

- Location: proceedings p260 (PDF p267)
- Retrieval tokens: systematic, presentation, triz, innovation
- Observed terms in first pages: method

**When to retrieve this**
- You need a compact mapping of TRIZ ideas into application options / reusable patterns.

**Possible MetaLens hooks (safe, non-claiming)**
- Link to ‘tool selection’ and ‘roadmap dispatch’ so users get the right method faster.
- Retrieval behavior: query this index first → jump to cited PDF page → extract only needed chunk → apply.

**Caution / failure modes**
- Don’t claim exact steps/quotes from this paper unless the PDF passage is retrieved and shown.
- In strict mode: if page/quote cannot be verified, respond with `REF_NOT_FOUND` and request the excerpt.

