# LLM Wiki Consumer

Virtual filesystem interface for LLM Agents to interact with wiki knowledge bases.

## Architecture

The system provides a **read-only virtual filesystem** (WikiFs) that translates filesystem-like commands into database queries:

- **Redis**: Stores path trees (compressed JSON) for directory operations (ls, find, tree)
- **Qdrant**: Stores wiki page chunks with metadata for content retrieval (cat, grep, head)
- **PostgreSQL**: Stores structured metadata (versions, users, conversations)

## WikiFs Commands

| Command | Description |
|---------|-------------|
| `ls(path)` | List directory contents from in-memory path tree |
| `cat(path)` | Read complete page: cache → chunks → sort → join → cache |
| `grep(pattern, path, flags)` | Three-stage search: coarse filter → prefetch → fine filter |
| `find(path, name_pattern)` | Find files by glob pattern in path tree |
| `tree(path, depth)` | Display directory tree structure |
| `head(path, lines)` | Read first N lines of a page |

## Quick Start

```bash
# Install dependencies
pip install -e ".[dev]"

# Start infrastructure services
docker-compose up -d

# Run tests
pytest tests/ -v

# Start the API server
uvicorn app.main:app --reload
```

## Project Structure

```
app/
├── main.py          # FastAPI entry point
├── config.py        # Pydantic Settings configuration
├── api/v1/          # REST API endpoints
├── core/wikifs.py   # WikiFs virtual filesystem core
├── models/schemas.py # Pydantic models
├── services/        # Business logic services
└── db/              # Database connections
tests/               # Unit tests
scripts/             # Utility scripts
```

## Development

```bash
# Run tests with coverage
pytest tests/ -v --cov=app

# Code formatting
ruff check app/ tests/

# Type checking
mypy app/
```