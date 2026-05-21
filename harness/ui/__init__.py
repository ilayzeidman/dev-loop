"""Web UI for dev-loop.

Pure stdlib HTTP server (``http.server``). No external deps. Serves a
single-page app from ``index.html`` plus a small JSON API:

  GET  /api/config                      -> resolved config
  GET  /api/runs                        -> list of task runs
  GET  /api/runs/<task-id>              -> task manifest
  GET  /api/runs/<task-id>/report       -> rendered Markdown report
  GET  /api/runs/<task-id>/report.json  -> structured report
  GET  /api/runs/<task-id>/iteration/<n>            -> iteration manifest
  GET  /api/runs/<task-id>/iteration/<n>/patch      -> patch.diff
  GET  /api/scenarios                   -> list of scenarios
  POST /api/implement                   -> kick off a new run (background)
  GET  /api/jobs/<job-id>               -> background job status

It's deliberately minimal: nothing here makes external network calls or
needs credentials.
"""

from .server import serve

__all__ = ["serve"]
