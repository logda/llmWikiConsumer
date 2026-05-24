#!/usr/bin/env python3
"""Create a test wiki package structure for integration testing.

Generates a directory structure with markdown files that simulates
a wiki knowledge base, then packages it as a .wiki.tar.gz archive.

Usage:
    python scripts/create_test_wiki.py [--output OUTPUT_DIR]

The generated structure:
    test-wiki/
    ├── wiki/
    │   ├── index.md
    │   ├── summaries/
    │   │   └── project-overview.md
    │   ├── entities/
    │   │   ├── auth-service.md
    │   │   └── user-service.md
    │   ├── concepts/
    │   │   ├── oauth.md
    │   │   ├── rbac.md
    │   │   └── sso.md
    │   └── synthesis/
    │       └── architecture.md
    └── meta.json
"""

import argparse
import gzip
import json
import os
import shutil
import tarfile
from pathlib import Path


# Wiki page content templates
PAGES: dict[str, str] = {
    "wiki/index.md": """\
# Wiki Index

Welcome to the project wiki. This is the central knowledge base for the project.

## Quick Links

- [Project Overview](summaries/project-overview.md)
- [OAuth 2.0](concepts/oauth.md)
- [RBAC](concepts/rbac.md)
- [SSO](concepts/sso.md)
- [Auth Service](entities/auth-service.md)
- [User Service](entities/user-service.md)
- [Architecture](synthesis/architecture.md)
""",
    "wiki/summaries/project-overview.md": """\
# Project Overview

This project is a microservices-based platform that provides authentication
and authorization services. The system uses OAuth 2.0 for delegated authorization
and RBAC for permission management.

## Key Components

- **Auth Service**: Handles token validation and RBAC checks
- **User Service**: Manages user profiles and role assignments
- **Gateway**: Routes requests and enforces authentication

## Technology Stack

- Python 3.11+ with FastAPI
- PostgreSQL for metadata
- Redis for caching
- Qdrant for vector search
""",
    "wiki/entities/auth-service.md": """\
# Auth Service

The Auth Service is responsible for validating OAuth tokens and performing
RBAC permission checks.

## Endpoints

### POST /auth/validate
Validates an OAuth access token and returns user claims.

Request:
```json
{
    "token": "eyJhbGciOiJSUzI1NiIs..."
}
```

Response:
```json
{
    "valid": true,
    "claims": {
        "sub": "user-123",
        "scope": "read write"
    }
}
```

### GET /auth/permissions/{user_id}
Returns the permissions for a user based on their RBAC roles.

## Configuration

- `OAUTH_ISSUER`: Token issuer URL
- `OAUTH_AUDIENCE`: Expected token audience
- `RBAC_POLICY_PATH`: Path to RBAC policy file
""",
    "wiki/entities/user-service.md": """\
# User Service

The User Service manages user profiles and role assignments.

## Endpoints

### GET /users/{user_id}
Returns user profile information.

### PUT /users/{user_id}/roles
Updates the role assignments for a user.

## Role Assignment Rules

1. A user can have multiple roles
2. Roles are inherited through the role hierarchy
3. Admin role has all permissions
4. Changes to roles are reflected immediately
""",
    "wiki/concepts/oauth.md": """\
# OAuth 2.0

OAuth 2.0 is an authorization framework that enables applications to obtain
limited access to user accounts on HTTP services.

## Grant Types

### Authorization Code
The most common grant type for server-side applications.

1. Client redirects user to authorization endpoint
2. User grants permission
3. Authorization server returns authorization code
4. Client exchanges code for access token

### Client Credentials
Used for server-to-server communication.

1. Client authenticates with client_id and client_secret
2. Authorization server returns access token directly

## Token Types

- **Access Token**: Short-lived token for API access
- **Refresh Token**: Long-lived token to obtain new access tokens

## Security Considerations

- Always use HTTPS
- Store tokens securely
- Validate token audience and issuer
- Use short token expiration times
""",
    "wiki/concepts/rbac.md": """\
# RBAC

Role-Based Access Control (RBAC) assigns permissions to roles, and roles
to users. This provides a flexible and manageable access control system.

## Role Hierarchy

```
admin
├── editor
│   └── viewer
└── operator
    └── viewer
```

## Permission Model

Each permission is defined as:
- **Resource**: The object being accessed (e.g., `wiki:page`)
- **Action**: The operation being performed (e.g., `read`, `write`, `delete`)

A permission string format: `resource:action` (e.g., `wiki:page:read`)

## Best Practices

1. Assign permissions to roles, not directly to users
2. Use the principle of least privilege
3. Regularly audit role assignments
4. Document the purpose of each role
""",
    "wiki/concepts/sso.md": """\
# SSO

Single Sign-On (SSO) allows users to authenticate once and access multiple
services without re-entering credentials.

## SAML 2.0

SAML 2.0 is an XML-based protocol for SSO.

### Flow
1. User attempts to access a service provider
2. Service provider redirects to identity provider
3. User authenticates with identity provider
4. Identity provider sends SAML assertion to service provider
5. Service provider grants access

## OpenID Connect

OpenID Connect (OIDC) is an identity layer on top of OAuth 2.0.

### Flow
1. Client redirects to authorization endpoint
2. User authenticates
3. Client receives ID token and access token
4. ID token contains user identity claims

## Configuration

- `SSO_PROVIDER`: The SSO protocol to use (saml2 or oidc)
- `SSO_ENTITY_ID`: The entity ID for this service
""",
    "wiki/synthesis/architecture.md": """\
# Architecture

The system follows a microservices architecture with an OAuth gateway.

## Component Diagram

```
[Client] --> [API Gateway] --> [Auth Service]
                          \--> [User Service]
                          \--> [Wiki Service]
```

## Data Flow

1. Client sends request with OAuth token
2. API Gateway validates token with Auth Service
3. Auth Service checks RBAC permissions
4. If authorized, request is forwarded to the target service
5. Response is returned to the client

## Deployment

- Services are containerized with Docker
- Orchestrated with docker-compose for development
- Kubernetes for production deployment

## Monitoring

- Health check endpoints on all services
- Prometheus metrics collection
- Grafana dashboards for visualization
""",
}


def create_wiki_structure(output_dir: Path) -> Path:
    """Create the wiki directory structure with markdown files.

    Args:
        output_dir: Directory to create the wiki structure in.

    Returns:
        Path to the created wiki root directory.
    """
    wiki_root = output_dir / "test-wiki"
    if wiki_root.exists():
        shutil.rmtree(wiki_root)

    # Create markdown files
    for rel_path, content in PAGES.items():
        file_path = wiki_root / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # Create meta.json
    meta = {
        "namespace": "test",
        "version": "v1",
        "description": "Test wiki package for integration testing",
        "file_count": len(PAGES),
        "files": list(PAGES.keys()),
    }
    meta_path = wiki_root / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return wiki_root


def build_path_tree(wiki_root: Path) -> dict:
    """Build the path tree data structure from the wiki files.

    Args:
        wiki_root: Path to the wiki root directory.

    Returns:
        Path tree dictionary with "files" and "directories" keys.
    """
    files: list[str] = []
    directories: dict[str, list[str]] = {}

    for md_file in sorted(wiki_root.rglob("*.md")):
        rel_path = md_file.relative_to(wiki_root)
        path_str = str(rel_path)
        files.append(path_str)

        # Build directory entries
        parts = path_str.split("/")
        for i in range(len(parts) - 1):
            dir_key = "/".join(parts[: i + 1]) + "/"
            if dir_key not in directories:
                # Collect entries for this directory
                dir_path = wiki_root / "/".join(parts[: i + 1])
                entries = sorted(
                    str(p.relative_to(dir_path)) + ("/" if p.is_dir() else "")
                    for p in dir_path.iterdir()
                )
                directories[dir_key] = entries

    return {"files": files, "directories": directories}


def create_tarball(wiki_root: Path, output_dir: Path) -> Path:
    """Create a .wiki.tar.gz archive from the wiki directory.

    Args:
        wiki_root: Path to the wiki root directory.
        output_dir: Directory to create the tarball in.

    Returns:
        Path to the created tarball.
    """
    tarball_path = output_dir / "test-wiki.wiki.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(wiki_root, arcname="test-wiki")
    return tarball_path


def create_path_tree_file(wiki_root: Path, output_dir: Path) -> Path:
    """Create a gzip-compressed JSON path tree file.

    Args:
        wiki_root: Path to the wiki root directory.
        output_dir: Directory to create the path tree file in.

    Returns:
        Path to the created path tree file.
    """
    path_tree = build_path_tree(wiki_root)
    json_bytes = json.dumps(path_tree, ensure_ascii=False).encode("utf-8")

    path_tree_path = output_dir / "path_tree_test_v1.json.gz"
    with gzip.open(path_tree_path, "wb") as f:
        f.write(json_bytes)

    # Also save uncompressed for inspection
    uncompressed_path = output_dir / "path_tree_test_v1.json"
    uncompressed_path.write_text(
        json.dumps(path_tree, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return path_tree_path


def main() -> None:
    """Main entry point for the test wiki creation script."""
    parser = argparse.ArgumentParser(description="Create a test wiki package")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent.parent / "test_data",
        help="Output directory for test data (default: test_data/)",
    )
    args = parser.parse_args()

    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Creating test wiki structure in {output_dir}...")

    # Step 1: Create wiki directory structure
    wiki_root = create_wiki_structure(output_dir)
    print(f"  Created wiki structure at {wiki_root}")
    print(f"  Generated {len(PAGES)} wiki pages")

    # Step 2: Create path tree
    path_tree = build_path_tree(wiki_root)
    print(f"  Built path tree with {len(path_tree['files'])} files")
    print(f"  and {len(path_tree['directories'])} directories")

    # Step 3: Create compressed path tree file
    path_tree_path = create_path_tree_file(wiki_root, output_dir)
    print(f"  Created path tree file at {path_tree_path}")

    # Step 4: Create tarball
    tarball_path = create_tarball(wiki_root, output_dir)
    print(f"  Created wiki tarball at {tarball_path}")

    print("\nDone! Test wiki package created successfully.")


if __name__ == "__main__":
    main()