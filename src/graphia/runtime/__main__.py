from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(payload: dict) -> dict:
    return {"echo": "stub", "received": payload}


if __name__ == "__main__":
    app.run(host="0.0.0.0")
