from .server import app


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=7860)


if __name__ == "__main__":
    main()
