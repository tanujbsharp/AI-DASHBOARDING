# AI Dashboarding Engine - Technical Specification

**Version:** 1.0  
**Last Updated:** December 2025  
**Status:** Active Development

---

## 1. System Overview

An AI-powered dashboarding engine that enables natural language querying of Elasticsearch/OpenSearch data. Users interact via a chat interface or Report Builder UI, and the system generates and executes Elasticsearch queries automatically.

### 1.1 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND (Angular)                       │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │ Chat UI     │  │ Report Builder  │  │ Results Display     │  │
│  │ (Natural    │  │ (Visual Query   │  │ (Tables, Aggs,      │  │
│  │  Language)  │  │  Builder)       │  │  Export)            │  │
│  └──────┬──────┘  └────────┬────────┘  └──────────┬──────────┘  │
│         │                  │                       │             │
│         └──────────────────┼───────────────────────┘             │
│                            │ HTTP (Proxy → :8000)                │
└────────────────────────────┼─────────────────────────────────────┘
                             │
┌────────────────────────────┼─────────────────────────────────────┐
│                    BACKEND (Django REST)                         │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │                    ChatView (API)                          │  │
│  │  POST /api/chat/ { message, history }                      │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │              ConversationService                           │  │
│  │  • Schema loading & enrichment                             │  │
│  │  • Context building (data knowledge)                       │  │
│  │  • Date intent extraction                                  │  │
│  │  • Conversation history management                         │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │                 AWS Bedrock (Claude)                       │  │
│  │  • Receives: System prompt + User message + History        │  │
│  │  • Returns: JSON with Elasticsearch query                  │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                     │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │                 OpenSearchClient                           │  │
│  │  • Executes generated query                                │  │
│  │  • Returns results + aggregations                          │  │
│  └───────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    AWS OpenSearch Cluster                         │
│  Index: converse_lm_consumption_summary_reports_qa               │
│  (~45,000 training completion records)                           │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Technology Stack

| Layer | Technology | Version |
|-------|------------|---------|
| Frontend | Angular | 19.x |
| Backend | Django REST Framework | 4.2.x |
| LLM | AWS Bedrock + Claude | anthropic.claude-3-5-sonnet |
| Database | AWS OpenSearch | 2.x |
| Language | TypeScript (FE), Python (BE) | 5.x, 3.13 |

---

## 3. Data Source

### 3.1 Active Index

| Index ID | OpenSearch Index Name |
|----------|----------------------|
| `module_consumption_data` | `converse_lm_consumption_summary_reports_qa` |

### 3.2 Schema Fields

| Field | Type | Description | Query Usage |
|-------|------|-------------|-------------|
| `email_addr` | keyword | User email address | Unique user counts |
| `first_name` | text | User first name | Display |
| `last_name` | text | User last name | Display |
| `city` | keyword | City (lowercase) | `{"term": {"city": "bengaluru"}}` |
| `country` | keyword | Country (full name) | `{"term": {"country": "united arab emirates"}}` |
| `module_name` | text | Training module name | `{"match": {"module_name": "..."}}` |
| `skill_name` | keyword | Skill category | Term filter |
| `product_name` | keyword | Product category | Term filter |
| `completed_status` | integer | 0=incomplete, 1=complete | `{"term": {"completed_status": 1}}` |
| `complete_percentage` | integer | 0-100 | Range filter |
| `created_on` | long | Unix timestamp | Date range filter |

### 3.3 Key Data Values

```
CITIES (lowercase):
- bengaluru (4,578), mumbai (1,628), chennai (632), delhi (996)
- ajman (1,277), al qusais (374) [UAE]

COUNTRIES:
- "republic of india" (21,904), "india" (2,855)
- "united arab emirates" (2,248)

COMPLETION STATUS:
- completed_status: 1 = completed (920 records)
- completed_status: 0 = not completed (44,077 records)
```

---

## 4. API Specification

### 4.1 Chat Endpoint

**POST** `/api/chat/`

#### Request
```json
{
  "message": "show completed trainings from bengaluru",
  "history": [
    {"role": "user", "content": "previous message"},
    {"role": "assistant", "content": "previous response"}
  ]
}
```

#### Response
```json
{
  "type": "query",
  "message": "Found 299 completion records from Bengaluru.",
  "index_id": "module_consumption_data",
  "query": {
    "_source": ["module_name", "email_addr", "city"],
    "query": {
      "bool": {
        "must": [
          {"term": {"city": "bengaluru"}},
          {"term": {"completed_status": 1}}
        ]
      }
    },
    "size": 50
  },
  "fields_to_show": ["module_name", "email_addr", "city"],
  "query_result": {
    "success": true,
    "total": 299,
    "count": 50,
    "results": [...],
    "took": 15,
    "aggregations": {}
  }
}
```

### 4.2 Other Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health/` | GET | Health check |
| `/api/indexes/` | GET | List available indexes |
| `/api/indexes/{id}/enriched-schema/` | GET | Get schema with samples |
| `/api/schemas/` | GET | Get all enriched schemas |
| `/api/schemas/reload/` | POST | Reload schemas from OpenSearch |

---

## 5. LLM Integration

### 5.1 System Prompt Structure

The system prompt sent to Claude includes:

1. **Data Knowledge** - Field descriptions, sample values, query patterns
2. **Date Context** - Current date, timestamp ranges for common periods
3. **Response Format** - JSON schema for query vs aggregation responses
4. **Critical Rules** - When to use tables vs aggregations

### 5.2 Query Generation Rules

| User Intent | Query Type | Example |
|-------------|-----------|---------|
| "list", "show", "give me", "table" | TABLE (size ≥ 20) | Returns rows with `_source` |
| "how many", "count", "total" | AGGREGATION (size: 0) | Returns `aggs` with counts |

### 5.3 Conversation Context

- Last 6 messages passed to Claude
- Follow-up detection for contextual queries
- Automatic filter combination from history

---

## 6. Frontend Components

### 6.1 Chat Interface (`app.component.ts`)

- Message history display
- Real-time query execution
- Results rendering (tables, aggregation cards)
- Export functionality

### 6.2 Report Builder (`prompt-playground.component.ts`)

| Panel | Function |
|-------|----------|
| Available Fields | Shows all schema fields with type badges |
| Selected Columns | Drag-drop ordering of output columns |
| Mega Filter | Quick presets (completed, last week, etc.) |
| Additional Filters | Field + operator + value filters |
| Prompt Preview | Live preview of generated natural language prompt |

---

## 7. File Structure

```
AI Dashboarding/
├── backend/
│   ├── config/
│   │   ├── settings.py          # Django settings, OpenSearch config
│   │   └── urls.py              # Root URL routing
│   ├── dashboarding/
│   │   ├── views.py             # API endpoints
│   │   ├── urls.py              # App URL routing
│   │   └── services/
│   │       ├── opensearch_client.py    # OpenSearch connection
│   │       ├── conversation_service.py # LLM orchestration
│   │       ├── bedrock_client.py       # AWS Bedrock client
│   │       └── query_builder.py        # Query utilities
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── app.component.ts        # Main chat UI
│   │   │   ├── components/
│   │   │   │   └── prompt-playground/  # Report Builder
│   │   │   └── services/
│   │   │       └── api.service.ts      # HTTP client
│   │   └── styles.scss                 # Global styles
│   └── proxy.conf.json                 # Dev proxy to backend
├── venv/                               # Python virtual environment
└── Memory Bank/                        # Documentation
```

---

## 8. Environment Variables

```bash
# AWS Configuration
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=xxx
AWS_SECRET_ACCESS_KEY=xxx

# Bedrock
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
USE_LLM=True

# OpenSearch
OPENSEARCH_DOMAIN_ENDPOINT=https://xxx.us-east-1.es.amazonaws.com
OPENSEARCH_MASTER_USER=xxx
OPENSEARCH_MASTER_PASS=xxx

# Indexes
MODULE_CONSUMPTION_DATA=converse_lm_consumption_summary_reports_qa
```

---

## 9. Running the Application

### 9.1 Backend

```bash
cd backend
source ../venv/bin/activate
python manage.py runserver 8000
```

### 9.2 Frontend

```bash
cd frontend
npm start
# Runs on http://localhost:4200 with proxy to :8000
```

---

## 10. Query Examples

### 10.1 Table Query (List Data)

**User:** "show completed trainings from bengaluru with user emails"

```json
{
  "_source": ["module_name", "email_addr", "first_name", "city"],
  "query": {
    "bool": {
      "must": [
        {"term": {"city": "bengaluru"}},
        {"term": {"completed_status": 1}}
      ]
    }
  },
  "size": 50
}
```

### 10.2 Aggregation Query (Count)

**User:** "how many unique users completed trainings from UAE?"

```json
{
  "size": 0,
  "query": {
    "bool": {
      "must": [
        {"term": {"country": "united arab emirates"}},
        {"term": {"completed_status": 1}}
      ]
    }
  },
  "aggs": {
    "unique_users": {"cardinality": {"field": "email_addr"}}
  }
}
```

### 10.3 Date Range Query

**User:** "completions in December 2025"

```json
{
  "query": {
    "bool": {
      "must": [
        {"term": {"completed_status": 1}},
        {"range": {"created_on": {"gte": 1733011200, "lt": 1735689600}}}
      ]
    }
  },
  "size": 50
}
```

---

## 11. Known Limitations

1. **Single Index Only** - Currently supports only `module_consumption_data`
2. **No Joins** - Cannot combine data from multiple indexes
3. **Field Names** - Aggregations must use field names without `.keyword` suffix
4. **Date Field** - Use `created_on` for date filtering (not `completed_date`)

---

## 12. Future Enhancements

- [ ] Multi-index support
- [ ] Saved reports / templates
- [ ] Scheduled report generation
- [ ] PDF/Excel export
- [ ] Dashboard embedding
- [ ] User authentication

