---
title: Using Claude Code with VT ARC Models
format: pdf
---

# Using Claude Code with the VT LLM Server

This proxy lets you use Claude Code with VT's LLM servers. It runs a local [LiteLLM](https://docs.litellm.ai) proxy in Docker that translates Claude Code's API format into requests forwarded to either the VT ARC server or an on-premises Ollama instance.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) installed and running
- [Claude Code](https://claude.ai/code) installed: `npm install -g @anthropic-ai/claude-code`

## Setup

### Step 1: Generate a Master Key

The proxy requires a `LITELLM_MASTER_KEY` for internal authentication. Generate one and add it to your `~/.bashrc`:

```bash
echo "export LITELLM_MASTER_KEY=$(./generate_master_key.sh)" >> ~/.bashrc
source ~/.bashrc
```

### Step 2: Get a VT API Key (VT ARC models only)

If you plan to use VT ARC models, you need a VT API key:

1. Go to `https://llm.arc.vt.edu` (OpenWebUI)
2. Log in with your VT credentials
3. Click your profile icon → **Settings** → **Account** → **API Keys**
4. Generate a new key and add it to your `~/.bashrc`:

```bash
echo "export VT_API_KEY=sk-your-vt-api-key" >> ~/.bashrc
source ~/.bashrc
```

The Ollama model (`glm-4.7-flash`) does not require a VT API key — skip this step if you only need that model.

## Usage

### VT ARC Models

Requires `VT_API_KEY` and `LITELLM_MASTER_KEY` set in your environment.

```bash
# Default model (Kimi-K2.5)
start_claude_vt

# Specify a model
start_claude_vt --model Kimi-K2.5
start_claude_vt --model gpt-oss-120b
start_claude_vt --model Qwen3.5-122B-A10B-FP8

# Pass API key directly (without environment variable)
start_claude_vt --api-key sk-your-vt-api-key
```

### Ollama Model

Requires only `LITELLM_MASTER_KEY`. No VT API key needed.

```bash
start_claude_vt --model glm-4.7-flash
```

## Available Models

| Model | Backend | VT API Key Required |
|---|---|---|
| `Kimi-K2.5` | VT ARC | Yes (default model) |
| `gpt-oss-120b` | VT ARC | Yes |
| `Qwen3.5-122B-A10B-FP8` | VT ARC | Yes |
| `glm-4.7-flash` | Ollama (ads1.datasci.vt.edu) | No |

## Stopping the Proxy

```bash
docker compose down
```

## How It Works

1. `start_claude_vt` (`/usr/bin/start_claude_vt`) starts a LiteLLM proxy in Docker on `localhost:4000`
2. Claude Code connects to the local proxy instead of Anthropic's servers
3. The proxy forwards requests to the appropriate backend (VT ARC or Ollama)
4. `LITELLM_MASTER_KEY` secures the proxy; `VT_API_KEY` authenticates to the VT ARC server

## Troubleshooting

**Error: `LITELLM_MASTER_KEY not set`**

```bash
export LITELLM_MASTER_KEY=$(./generate_master_key.sh)
```

**Warning: `VT_API_KEY not set`**

Only required for VT ARC models. The Ollama model (`glm-4.7-flash`) works without it.

```bash
export VT_API_KEY=sk-your-vt-api-key
```

**Proxy did not become ready in time**

Check Docker logs:

```bash
docker compose logs
```
