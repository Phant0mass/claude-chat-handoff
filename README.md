# Claude.ai Chat Handoff Generator

**Recover context from stuck or dead Claude.ai chat sessions.**

When Claude.ai's context window fills up or auto-compaction fails, you can't send messages anymore. This tool extracts your conversation and generates a compressed handoff document to bootstrap a new session.

## The Problem

- Claude.ai has a bug where messages fail silently when context approaches capacity
- Auto-compaction sometimes fails to trigger, leaving the chat window stuck - currently happens in both browser and desktop app UI
- No way to continue the conversation or recover your context easily to bootstrap a new chat window quickly
- **Related issues:** [#18676](https://github.com/anthropics/claude-code/issues/18676), [#18866](https://github.com/anthropics/claude-code/issues/18866)

## Features

- **JSON input → MD output** - Complete data in, compact handoff out
- **Smart mode (default)** - AI-powered summarization via Claude API (~90% compression)
- **Standard mode** - Offline fallback with algorithmic extraction (~50% compression)
- **Noise filtering** - Removes system messages (compaction notices, status updates)
- **Claude.ai summary extraction** - Uses the built-in summary field from JSON exports

## Why JSON Only?

Research comparing JSON vs Markdown exports revealed significant differences:

| Aspect | JSON Export | Markdown Export |
|--------|-------------|-----------------|
| **Claude.ai summary** | ✅ Built-in `summary` field | ❌ Not available |
| **Branch data** | ✅ All conversation branches | ❌ Current branch only |
| **Model info** | ✅ Preserved | ❌ Lost |
| **Message parsing** | ✅ Clean, structured | ⚠️ Artifact contamination |
| **Data completeness** | ✅ Complete | ❌ Lossy |

**Key insight:** Claude.ai generates a `summary` field in JSON exports automatically - this provides excellent context that MD exports don't have.

**Compression still works:** Although JSON inputs are ~5x larger than MD, the handoff tool compresses them into compact MD outputs (~90% reduction).

## Quick Start

### 1. Install a Chat Export Extension

**Chrome (recommended):**

- [Claude Conversation Exporter](https://github.com/socketteer/Claude-Conversation-Exporter) - Open source, feature-rich
- [AI Chat Exporter](https://chromewebstore.google.com/detail/ai-chat-exporter-save-cla/elhmfakncmnghlnabnolalcjkdpfjnin) - Commercial, polished UI

**Firefox:**

- [Claude Exporter](https://addons.mozilla.org/en-US/firefox/addon/claude-exporter/)

### 2. Export Your Chat as JSON

Open the stuck chat in Claude.ai, click the extension icon:

| Setting | Value |
|---------|-------|
| **Export format** | **JSON** (not Markdown!) |
| **Include thinking** | ✅ Checked |
| **Include metadata** | ✅ Checked |
| Include artifacts | In Original or JSON format |

Save the `.json` file to the `exports/` folder (you can change the folders names and or locations at top of the handoff.py script.)

### 3. Configure API Key (for Smart Mode)

Copy `config.example.json` to `config.json`:

```bash
cp config.example.json config.json
```

Edit `config.json` and add your Anthropic API key:

```json
{
  "anthropic_api_key": "sk-ant-api03-YOUR-KEY-HERE",
  "smart_mode": {
    "model": "claude-sonnet-4-20250514",
    "verbatim_recent_messages": 30,
    "max_output_tokens": 4096
  }
}
```

Get your API key at [platform.claude.com/](https://platform.claude.com/)

### 4. Run the Tool

```bash
python handoff.py
```

Or on Windows, double-click `handoff.py` (requires Python in PATH).

## Usage Example

```
============================================================
  CLAUDE.AI CHAT HANDOFF GENERATOR
  Recover context from stuck chat sessions
============================================================

  Input:  JSON exports only (complete data, built-in summary)
  Output: Compressed Markdown handoff (~90% smaller)

Input directory: /path/to/exports

Available JSON exports (most recent first):
  [1] My-Project-Chat.json (566 KB)
  [2] Another-Chat.json (234 KB)

Enter number, filename, or full path:
> 1

Selected: My-Project-Chat.json

Parsing JSON export...
  Chat name: My Project Chat
  Messages: 68 (after noise filtering)
  Estimated tokens: ~18,432
  Claude.ai summary: ✓ Available

------------------------------------------------------------
Processing Mode:

  [1] Smart    - AI-powered summarization (DEFAULT)
                 ~90% compression, structured output
  [2] Standard - Algorithmic extraction (offline)
                 ~50% compression, basic extraction

  Chat size: ~18,432 tokens

Select mode [1]:

Generating handoff...
  ✓ Calling Claude API for smart summarization... done!

============================================================
  DONE!
============================================================
  Mode:        SMART
  Input:       566 KB (JSON)
  Output:      33 KB (MD)
  Compression: 94%
  Saved:       2026-01-21-handoff-my-project-chat-smart.md
  Path:        /path/to/handoffs/2026-01-21-handoff-my-project-chat-smart.md

Use this handoff to bootstrap a new Claude session.
```

## Output Structure

The generated handoff includes:

```markdown
# Handoff: Chat Name

**Generated:** 2026-01-21 00:47
**Mode:** Smart (AI-summarized)
**Message Count:** 76 (46 summarized, 30 verbatim)
**Original Size:** ~18,432 tokens

## Quick Context (Claude.ai Auto-generated)
[Built-in summary from JSON export - high quality!]

## AI-Summarized Earlier Content
### Session Overview
### Key Accomplishments
### Architecture Decisions
### Files Modified
### Current State
### Known Issues / TODOs
### Critical Context for Continuation

## Recent Conversation (Verbatim)
[Last 30 messages preserved for immediate context]

## Resumption Instructions
```

## Mode Comparison

| Aspect | Smart (Default) | Standard |
|--------|-----------------|----------|
| **Compression** | ~90% | ~50% |
| **Speed** | 30-60 seconds | Instant |
| **Cost** | ~$0.05-0.30 | Free |
| **Offline** | ❌ Needs API | ✅ Yes |
| **Quality** | Excellent | Good |

**Use Standard when:** offline, no API key, or testing.

## Noise Filtering

The tool automatically filters out system noise:

- Compaction notices (`Compacting conversation...`)
- Claude Code status updates (`✶ Churned for 5m...`)
- Checkbox status lines (`⎿ ☐ Task item`)
- Other system messages

This keeps the handoff focused on actual conversation content.

## Directory Structure

```
handoff/
├── handoff.py           # Main script
├── config.json          # Your API key (create from example)
├── config.example.json  # Template
├── README.md            # This file
├── exports/             # Put exported chat files here
└── handoffs/            # Generated handoff documents
```

## Requirements

- Python 3.10+ (tested with 3.14)
- No pip packages required (uses stdlib only)
- Anthropic API key (for Smart mode only)

## Cost Reference

| Chat Size | Smart Mode Cost |
|-----------|-----------------|
| 20K tokens | ~$0.05 |
| 75K tokens | ~$0.25 |
| 150K tokens | ~$0.50 |

Uses Claude Sonnet 4 ($3/1M input, $15/1M output).

## Troubleshooting

### "No JSON exports found"
- Make sure you exported as **JSON**, not Markdown
- Check the file is in the `exports/` directory
- The extension should be `.json`

### "Claude.ai summary: Not available"
- This can happen with very old exports or some export tools
- The handoff will still work, just without the Quick Context section
- Try a different export extension

### "Invalid JSON format"
- The JSON file may be corrupted or from an incompatible exporter
- Try re-exporting the chat
- Make sure you're using a Claude.ai chat exporter (not a generic tool)

### API errors
- Verify your API key in `config.json`
- Check your Anthropic account has credits
- Try Standard mode as fallback

### Spinner seems stuck
- API calls can take 30-120 seconds for large chats
- Wait for it to complete
- If it takes more than 2 minutes, there may be a network issue

## Tips

- **Export early** - Don't wait until the chat is completely stuck
- **Use Smart mode** - Even small chats benefit from AI summarization
- **Review the handoff** - May contain decisions or context you want to edit
- **Delete exports after** - They can be large (500KB+)
- **Keep handoffs** - They're useful documentation of your work

## Contributing

This is a community tool. Feel free to:
- Report bugs
- Suggest improvements
- Share your modifications

## License

MIT - Use freely, no attribution required.

---

*Created to help the Claude.ai community while platform improvements are in progress.*
