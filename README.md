# AI Dashboard Engine

An AI-powered dashboarding engine that dynamically discovers OpenSearch schemas and uses AWS Bedrock with Claude to generate Elasticsearch queries from natural language.

## Features

- **Dynamic Schema Discovery** - Automatically fetches field mappings from OpenSearch indexes
- **Natural Language Query Generation** - Uses Claude AI to convert your selections into Elasticsearch queries
- **Interactive Prompt Playground** - Build reports with filters, column selection, and sorting
- **Zoho-style Table Reports** - Clean, exportable data tables with CSV export

## Tech Stack

- **Backend:** Django 4.2 + Django REST Framework
- **Frontend:** Angular 19 (standalone components)
- **AI:** AWS Bedrock with Claude
- **Database:** OpenSearch

## Prerequisites

- Python 3.10+
- Node.js 18+
- AWS account with Bedrock access
- OpenSearch cluster

## Setup

### 1. Environment Variables

Ensure your `.env` file contains:

```env
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
USE_LLM=true

OPENSEARCH_DOMAIN_ENDPOINT=https://your-opensearch-endpoint
OPENSEARCH_MASTER_USER=your_username
OPENSEARCH_MASTER_PASS=your_password

MODULE_CONSUMPTION_DATA=converse_lm_consumption_summary_reports_qa
```

### 2. Backend Setup

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r backend/requirements.txt

# Run the Django server
cd backend
python manage.py runserver
```

The API will be available at `http://localhost:8000`

### 3. Frontend Setup

```bash
# Install dependencies
cd frontend
npm install

# Run the Angular dev server
npm start
```

The UI will be available at `http://localhost:4200`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/indexes/` | GET | List all available indexes |
| `/api/indexes/{id}/schema/` | GET | Get field mappings for an index |
| `/api/indexes/{id}/sample-values/{field}/` | GET | Get sample values for filter suggestions |
| `/api/mega-filters/` | GET | Get available time-based filters |
| `/api/query/build-prompt/` | POST | Build a prompt from UI selections |
| `/api/query/generate/` | POST | Generate Elasticsearch query via Claude |
| `/api/query/execute/` | POST | Execute query and return results |

## Usage

1. **Select a Data Source** - Choose from the 4 configured OpenSearch indexes
2. **Select Columns** - Click fields in the schema panel to add them as table columns
3. **Configure Filters** - Set time period (mega filter) and additional field filters
4. **Generate & Execute** - Click "Generate Query & Execute" to run the AI-generated query
5. **Export Results** - Download results as CSV

## Project Structure

```
AI Dashboarding/
├── backend/
│   ├── config/              # Django settings
│   ├── dashboarding/        # Main app
│   │   ├── services/        # OpenSearch, Bedrock clients
│   │   ├── views.py         # API endpoints
│   │   └── urls.py          # URL routing
│   ├── manage.py
│   └── requirements.txt
├── frontend/
│   └── src/app/
│       ├── components/      # Angular components
│       ├── services/        # API service
│       └── models/          # TypeScript interfaces
├── .env                     # Environment variables
└── README.md
```

## Development

### Running Both Servers

Terminal 1 (Backend):
```bash
source venv/bin/activate
cd backend
python manage.py runserver
```

Terminal 2 (Frontend):
```bash
cd frontend
npm start
```

### Disable LLM for Testing

Set `USE_LLM=false` in your `.env` to use fallback queries without calling Claude.

