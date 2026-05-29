import uvicorn


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8888"))
    host = os.getenv("HOST", "0.0.0.0")
    # Auto-reload in dev so code/template edits take effect without a manual
    # restart (a stale process silently 404s newly-added routes). Off in
    # production. Override explicitly with RELOAD=1 / RELOAD=0.
    _reload_default = os.getenv("ENV", "development") != "production"
    reload = os.getenv("RELOAD", "1" if _reload_default else "0") == "1"
    uvicorn.run(
        "src.web.app:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["src"] if reload else None,
    )
