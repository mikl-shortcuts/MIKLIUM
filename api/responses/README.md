# Miklium LLM Responses API Documentation

## Navigation

- [Miklium LLM Responses API Documentation](#miklium-llm-responses-api-documentation)
  - [About MIKLIUM Responses](#about-miklium-responses)
  - [Request Body](#request-body)
    - [POST Method](#post-method)
  - [Code Examples](#code-examples)
    - [JavaScript (OpenAI-like SDK Style)](#javascript-openai-like-sdk-style)
  - [API Responses](#api-responses)
    - [Success](#success)
    - [Error](#error)

## About MIKLIUM Responses

This API provides access to the Miklium LLM models, starting with **miklium-lm-nano**. It is designed to be easy to use and follows a clean request structure similar to the OpenAI SDK.

## Request Body

Link: `https://miklium.vercel.app/api/responses`

| Parameter | Required | Type | Description |
| :--- | :--- | :--- | :--- |
| `input` | Yes | String | The prompt text to send to the model. |
| `model` | No | String | The model ID (currently only `miklium-lm-nano`). |

### POST Method

```json
{
  "model": "miklium-lm-nano",
  "input": "Write a short bedtime story about a unicorn."
}
```

## Code Examples

### JavaScript (OpenAI-like SDK Style)

You can use the following pattern to call the API in a way that feels like the OpenAI SDK:

```javascript
class MikliumAI {
  constructor(config = {}) {
    this.baseURL = config.baseURL || "https://miklium.vercel.app/api/responses";
  }

  get responses() {
    return {
      create: async (data) => {
        const res = await fetch(this.baseURL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        });
        if (!res.ok) throw new Error(await res.text());
        return res.json();
      }
    };
  }
}

const client = new MikliumAI();

const response = await client.responses.create({
  model: "miklium-lm-nano",
  input: "Write a short bedtime story about a unicorn.",
});

console.log(response.output_text);
```

### Python (Requests)

```python
import requests

r = requests.post("https://miklium.vercel.app/api/responses",
                   json={"model": "miklium-lm-nano", "input": "Write a short bedtime story about a unicorn."})
print(r.json()["output_text"])
```

## API Responses

### Success

| Parameter | Value |
| :--- | :--- |
| `success` | `true` |
| `model` | `String`, The model used |
| `output_text` | `String`, The generated response |
| `usage` | `Object`, Token usage information |

```json
{
  "success": true,
  "model": "miklium-lm-nano",
  "output_text": "Once upon a time, in a land of rainbows...",
  "usage": {
    "total_tokens": 42
  }
}
```

### Error

| Parameter | Value |
| :--- | :--- |
| `success` | `false` |
| `error` | `String`, Error message |

```json
{
  "success": false,
  "error": "Missing 'input' field"
}
```
