import uvicorn


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8888"))
    uvicorn.run("src.web.app:app", host="127.0.0.1", port=port, reload=False)
