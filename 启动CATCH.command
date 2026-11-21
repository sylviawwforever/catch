#!/bin/bash
cd "$(dirname "$0")"
PORT=8900
( sleep 1; open "http://localhost:${PORT}/index.html" ) &
python3 tools/serve.py ${PORT}
