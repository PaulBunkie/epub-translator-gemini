# Team Logo API Documentation

## Overview

Team logos are stored in the `team_registry.db` database and served via a dedicated Flask endpoint. The logos are PNG images that are fetched from SofaScore API during the team registry building process.

## Logo Storage

### Database Schema
- **Table**: `teams` in `team_registry.db`
- **Column**: `logo_data` (BLOB) - stores PNG image data
- **Column**: `sofascore_team_id` (INTEGER) - unique identifier from SofaScore API

### Logo Sources
1. **Local Archive**: `static/team_logos_archive/` - folder structure with PNG logos organized by league
2. **SofaScore API**: Real team IDs and logos fetched during team registry building

## API Endpoint

### Endpoint: `/api/team-logo/<sofascore_team_id>`

**Method**: `GET`

**Parameters**:
- `sofascore_team_id` (int, required) - SofaScore team ID

**Response**:
- **Success (200)**: PNG image data with `Content-Type: image/png`
- **Not Found (204)**: Empty response when logo doesn't exist

### Implementation

```python
@app.route('/api/team-logo/<int:sofascore_team_id>')
def api_team_logo(sofascore_team_id):
    """Serves team logo from team_registry.db by SofaScore team ID."""
    import sqlite3 as _sqlite3
    
    # Connect to team registry database
    registry_path = str(TEAM_REGISTRY_DB_FILE)
    conn = _sqlite3.connect(registry_path)
    conn.row_factory = _sqlite3.Row
    
    # Query for logo data
    row = conn.execute(
        'SELECT logo_data FROM teams WHERE sofascore_team_id = ?',
        (sofascore_team_id,)
    ).fetchone()
    
    conn.close()
    
    # Return logo or empty response
    if not row or not row['logo_data']:
        return make_response('', 204)  # No Content
    
    return Response(row['logo_data'], mimetype='image/png')
```

## Usage in Frontend

### HTML Template Example

In `templates/bet.html`, team logos are displayed inline with team names:

```html
<!-- Home team logo -->
${match.home_team_sofascore_id ? 
  `<img src="/api/team-logo/${match.home_team_sofascore_id}" 
        class="team-logo" 
        alt="" 
        onerror="this.style.display='none'"> ` : ''}
${match.home_team}

<!-- Away team logo -->
${match.away_team_sofascore_id ? 
  `<img src="/api/team-logo/${match.away_team_sofascore_id}" 
        class="team-logo" 
        alt="" 
        onerror="this.style.display='none'"> ` : ''}
${match.away_team}
```

### CSS Styling

```css
.team-logo {
    width: 20px;
    height: 20px;
    vertical-align: middle;
    margin-right: 5px;
}
```

## Request Procedure

### Step-by-Step Flow

1. **Frontend Request**
   - Browser requests `/api/team-logo/{team_id}`
   - Example: `/api/team-logo/12345`

2. **Flask Route Handler**
   - Receives `sofascore_team_id` parameter
   - Connects to `team_registry.db`

3. **Database Query**
   - Executes: `SELECT logo_data FROM teams WHERE sofascore_team_id = ?`
   - Returns BLOB data if found, None otherwise

4. **Response**
   - **If logo exists**: Returns PNG image with `Content-Type: image/png`
   - **If no logo**: Returns 204 No Content (empty response)
   - Frontend `onerror` handler hides broken images

### Example Request/Response

```
Request:
GET /api/team-logo/12345 HTTP/1.1
Host: example.com

Response (Success):
HTTP/1.1 200 OK
Content-Type: image/png
Content-Length: 4521

[binary PNG data]

Response (Not Found):
HTTP/1.1 204 No Content
```

## Building Team Registry with Logos

To populate the logo database, use the `build_team_registry.py` script:

```bash
# Build registry from local archive and SofaScore API
python build_team_registry.py
```

This script:
1. Scans `static/team_logos_archive/` for PNG logos organized by league folders
2. Queries SofaScore API to get real team IDs by name
3. Inserts or updates teams in `team_registry.db` with logo data

## Data Flow Diagram

```
┌─────────────┐      ┌──────────────┐      ┌───────────────┐
│   Browser   │─────▶│  Flask App   │─────▶│ team_registry │
│             │      │  /app.py     │      │     .db       │
└─────────────┘      └──────────────┘      └───────────────┘
      │                      │                       │
      │  GET /api/team-logo/12345                   │
      │──────────────────────────────────────────────│
      │                      │                       │
      │                      │  SELECT logo_data     │
      │                      │◀──────────────────────│
      │                      │                       │
      │  200 OK + PNG        │                       │
      │◀─────────────────────│                       │
      │                      │                       │
└─────────────┘      └──────────────┘      ┌───────────────┘
                                          │
                                          │ If not found:
                                          │ - Return 204 No Content
                                          │ - Frontend hides <img>
                                          │
```

## Error Handling

- **No logo in DB**: Returns HTTP 204 (No Content)
- **Frontend fallback**: `onerror="this.style.display='none'"` hides broken images
- **Invalid team ID**: Returns 204 (no crash)
- **Database error**: Logged to console, returns 500

## Configuration

- **Database path**: Defined in `config.py` as `TEAM_REGISTRY_DB_FILE`
- **Logo archive**: `static/team_logos_archive/`
- **MIME type**: `image/png` (hardcoded)
- **Max logo size**: Limited by database BLOB field size

## Related Files

- `app.py` - Flask route handler (`api_team_logo`)
- `build_team_registry.py` - Script to build/populate team registry
- `templates/bet.html` - Frontend usage example
- `config.py` - Database path configuration