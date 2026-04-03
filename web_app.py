import uvicorn


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8888"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("src.web.app:app", host=host, port=port, reload=False)
