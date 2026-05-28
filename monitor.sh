#!/usr/bin/env bash
# Watch the mesh think — the "Monitor" node (substrate A). Run in its own pane.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${ROOKERY_DB:-$DIR/rookery.db}"
exec watch -n1 "sqlite3 -header -column '$DB' \
  'SELECT id,sender,recipient,substr(body,1,40) AS body,delivered FROM inbox ORDER BY id DESC LIMIT 12;' ; \
 echo ; echo NODES: ; sqlite3 -header -column '$DB' 'SELECT node_id,kind,status,pid,last_seen FROM nodes;' ; \
 echo ; echo CREDENTIALS: ; sqlite3 -header -column '$DB' 'SELECT id,node_id,resource,status,token_ref FROM credential_requests;'"
