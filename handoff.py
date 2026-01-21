#!/usr/bin/env python3
"""
Claude.ai Chat Handoff Generator
================================

Recover context from stuck/dead Claude.ai chat windows.

INPUT FORMAT: JSON ONLY
  Research shows JSON exports are superior to Markdown exports:
  - JSON includes Claude.ai's built-in summary field (auto-generated)
  - JSON preserves complete branch data (MD only exports current branch)
  - JSON retains model information (lost in MD exports)
  - JSON has clean message parsing (MD often corrupts artifact content)

  The handoff tool compresses JSON input into a compact MD output,
  giving you the best of both worlds: complete data in, concise handoff out.

MODES:
  - Smart (default): AI-powered summarization via Claude API (~90% compression)
  - Standard: Algorithmic extraction (offline fallback, ~50% compression)

SETUP:
  1. Install a chat export browser extension (see README)
  2. Export your chat as JSON (NOT Markdown)
  3. Save to 'exports/' directory
  4. Copy config.example.json to config.json, add your API key
  5. Run: python handoff.py

GitHub: https://github.com/Phant0mass/claude-chat-handoff
License: MIT
"""

import json
import sys
import os
import re
import urllib.request
import urllib.error
import threading
import time
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================
SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_INPUT_DIR = SCRIPT_DIR / "exports"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "handoffs"
CONFIG_FILE = SCRIPT_DIR / "config.json"

# Defaults
VERBATIM_MESSAGE_COUNT = 30
SMART_MODEL = "claude-sonnet-4-20250514"
SMART_MAX_TOKENS = 4096

# Noise patterns to filter from messages (Claude Code status, compaction notices, etc.)
NOISE_PATTERNS = [
    r'^✶\s*Compacting conversation',
    r'^✶\s*Churned for',
    r'^✶\s*Sautéed for',
    r'^✶\s*Brewed for',
    r'^✶\s*Worked for',
    r'^✶\s*Zigzagging',
    r'^\s*⎿\s*☐',  # Checkbox items from CC status
    r'^\s*⎿\s*☒',  # Checked items from CC status
    r'^ctrl\+c to interrupt',
    r'thinking\)$',
]


def load_config():
    """Load config from config.json if exists"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ============================================================================
# JSON PARSING
# ============================================================================

def parse_json_export(filepath):
    """Parse JSON export format from Claude.ai"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Validate structure
    if 'chat_messages' not in data and 'messages' not in data:
        raise ValueError("Invalid JSON format: missing 'chat_messages' or 'messages' field")

    messages = []
    raw_messages = data.get('chat_messages', data.get('messages', []))

    for msg in raw_messages:
        content = extract_content_from_json(msg)

        # Skip empty messages
        if not content.strip():
            continue

        # Filter noise from content
        content = filter_noise(content)

        # Skip if content became empty after filtering
        if not content.strip():
            continue

        # Skip entire message if it's just noise
        if is_noise_message(content):
            continue

        messages.append({
            'sender': msg.get('sender', 'unknown'),
            'content': content,
            'timestamp': msg.get('created_at', '')[:19].replace('T', ' ')
        })

    return {
        'name': data.get('name', 'Unknown Chat'),
        'summary': data.get('summary', ''),  # Claude.ai's built-in summary - this is gold!
        'created': data.get('created_at', '')[:10],
        'updated': data.get('updated_at', '')[:10],
        'messages': messages
    }


def extract_content_from_json(msg):
    """Extract text content from JSON message"""
    content = msg.get('content', [])
    if not content:
        return msg.get('text', '') or ''

    texts = []
    for item in content:
        if isinstance(item, dict):
            text = item.get('text', '')
            if text:
                texts.append(text)
        elif isinstance(item, str):
            texts.append(item)
    return '\n'.join(texts)


def filter_noise(content):
    """Filter out system noise from message content"""
    lines = content.split('\n')
    filtered_lines = []

    for line in lines:
        is_noise = False
        for pattern in NOISE_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                is_noise = True
                break

        if not is_noise:
            filtered_lines.append(line)

    return '\n'.join(filtered_lines)


def is_noise_message(content):
    """Check if entire message is noise (CC status updates, etc.)"""
    content = content.strip()

    # Very short messages that are just status updates
    if len(content) < 100:
        noise_indicators = [
            'Compacting conversation',
            'ctrl+c to interrupt',
            '⎿',  # CC status indicator
            '✶',  # CC status indicator
        ]
        for indicator in noise_indicators:
            if indicator in content:
                return True

    return False


# ============================================================================
# CORE FUNCTIONS
# ============================================================================

def estimate_tokens(text_or_count):
    """Rough token estimate: ~4 chars per token"""
    if isinstance(text_or_count, int):
        return text_or_count // 4
    return len(text_or_count or '') // 4


def extract_file_paths(text):
    """Extract file paths mentioned in text"""
    patterns = [
        r'[A-Za-z]:\\[^\s\'"<>|]+',  # Windows paths
        r'/[\w\-./]+\.\w+',           # Unix paths with extension
        r'src/[\w\-./]+',             # Common source paths
        r'server/[\w\-./]+',
        r'docs/[\w\-./]+',
        r'lib/[\w\-./]+',
        r'app/[\w\-./]+',
        r'components/[\w\-./]+',
    ]

    paths = set()
    for pattern in patterns:
        matches = re.findall(pattern, text)
        paths.update(matches)

    filtered = [p for p in paths if len(p) > 5 and not p.startswith('//')]
    return sorted(filtered)


def extract_decisions(text):
    """Extract lines that look like decisions or key points"""
    decision_markers = [
        r'(?:we|I) (?:decided|chose|will|should|need to)',
        r'(?:the|our) pattern',
        r'convention',
        r'architecture',
        r'migration',
        r'IMPORTANT',
        r'NOTE:',
        r'TODO:',
    ]

    decisions = []
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if len(line) > 20 and len(line) < 500:
            for marker in decision_markers:
                if re.search(marker, line, re.IGNORECASE):
                    decisions.append(line)
                    break

    return decisions[:20]


# ============================================================================
# SMART MODE - Claude API
# ============================================================================

class Spinner:
    """Simple CLI spinner for long-running operations"""
    def __init__(self, message="Working"):
        self.message = message
        self.running = False
        self.thread = None

    def _spin(self):
        chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        while self.running:
            print(f"\r  {chars[i % len(chars)]} {self.message}...", end="", flush=True)
            time.sleep(0.1)
            i += 1
        print(f"\r  ✓ {self.message}... done!     ")

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._spin)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()


def call_claude_api(api_key, prompt, model=SMART_MODEL, max_tokens=SMART_MAX_TOKENS):
    """Call Claude API for smart summarization"""
    url = "https://api.anthropic.com/v1/messages"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }

    data = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers=headers,
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result['content'][0]['text']
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        raise Exception(f"API Error {e.code}: {error_body}")
    except Exception as e:
        raise Exception(f"API call failed: {e}")


def generate_smart_summary(messages, api_key, config):
    """Use Claude API to intelligently summarize older messages"""

    model = config.get('smart_mode', {}).get('model', SMART_MODEL)
    max_tokens = config.get('smart_mode', {}).get('max_output_tokens', SMART_MAX_TOKENS)

    # Build conversation text for summarization
    conv_parts = []
    for msg in messages:
        sender = "Human" if msg['sender'] == 'human' else "Assistant"
        content = msg['content'][:5000]  # Truncate very long messages
        conv_parts.append(f"[{sender}]: {content}")

    conversation_text = "\n\n".join(conv_parts)

    # Truncate if still too long (with warning)
    if len(conversation_text) > 350000:
        print("  ⚠ Large conversation - truncating for API call")
        conversation_text = conversation_text[:350000] + "\n\n[...truncated due to size...]"

    prompt = f"""You are analyzing a development conversation to create a handoff document for resuming work in a new chat session.

CONVERSATION TO ANALYZE:
{conversation_text}

Create a structured summary with these sections:

## Session Overview
Brief 2-3 sentence summary of what was accomplished.

## Key Accomplishments
Bullet list of concrete things completed (files created, features implemented, bugs fixed).

## Architecture Decisions
Important technical decisions made, patterns established, conventions adopted.

## Files Modified
List the key files that were created or modified (just paths, no descriptions).

## Current State
Where things stand at the end of this portion of the conversation - what works, what's in progress.

## Known Issues / TODOs
Any bugs, incomplete items, or next steps mentioned.

## Critical Context for Continuation
Any specific details (variable names, patterns, gotchas) that would be essential for continuing this work.

Be concise but complete. Focus on actionable information for resuming work."""

    spinner = Spinner("Calling Claude API for smart summarization")
    spinner.start()
    try:
        result = call_claude_api(api_key, prompt, model, max_tokens)
    finally:
        spinner.stop()
    return result


# ============================================================================
# HANDOFF GENERATION
# ============================================================================

def generate_handoff_standard(data):
    """Generate handoff using algorithmic approach (offline fallback)"""

    messages = data['messages']
    all_text = '\n'.join(m['content'] for m in messages)
    file_paths = extract_file_paths(all_text)
    decisions = extract_decisions(all_text)

    lines = []

    # Header
    lines.append(f"# Handoff: {data['name']}")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Mode:** Standard (algorithmic - offline)")
    lines.append(f"**Original Chat Period:** {data['created']} to {data['updated']}")
    lines.append(f"**Message Count:** {len(messages)}")
    lines.append(f"**Estimated Tokens:** ~{estimate_tokens(all_text):,}")
    lines.append("")

    # Claude.ai's built-in summary (if available)
    if data['summary']:
        lines.append("## Quick Context (Claude.ai Auto-generated)")
        lines.append("")
        lines.append(data['summary'])
        lines.append("")

    # Files
    if file_paths:
        lines.append("## Files Referenced")
        lines.append("")
        for path in file_paths[:30]:
            lines.append(f"- `{path}`")
        lines.append("")

    # Decisions
    if decisions:
        lines.append("## Key Decisions & Patterns")
        lines.append("")
        for decision in decisions:
            lines.append(f"- {decision.strip()}")
        lines.append("")

    # Recent messages
    lines.append("## Recent Conversation (Verbatim)")
    lines.append("")
    lines.append(f"*Last {min(VERBATIM_MESSAGE_COUNT, len(messages))} messages preserved:*")
    lines.append("")

    for msg in messages[-VERBATIM_MESSAGE_COUNT:]:
        sender_label = "**Human:**" if msg['sender'] == 'human' else "**Assistant:**"
        lines.append(f"### {sender_label} ({msg['timestamp']})")
        lines.append("")
        content = msg['content']
        if len(content) > 8000:
            content = content[:8000] + f"\n\n*[Truncated - original was {len(msg['content']):,} chars]*"
        lines.append(content)
        lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append("## Resumption Instructions")
    lines.append("")
    lines.append("1. Share this handoff document with Claude in a new chat")
    lines.append("2. Reference any relevant project documentation")
    lines.append("3. Specify which aspect of the work to continue")
    lines.append("")

    return '\n'.join(lines)


def generate_handoff_smart(data, api_key, config):
    """Generate handoff using AI-powered summarization (default)"""

    messages = data['messages']
    verbatim_count = config.get('smart_mode', {}).get('verbatim_recent_messages', VERBATIM_MESSAGE_COUNT)

    total_chars = sum(len(m['content']) for m in messages)

    # Split: older messages for AI summary, recent messages verbatim
    older_messages = messages[:-verbatim_count] if len(messages) > verbatim_count else []
    recent_messages = messages[-verbatim_count:]

    # Get AI summary of older messages
    ai_summary = ""
    if older_messages:
        ai_summary = generate_smart_summary(older_messages, api_key, config)

    # Build handoff
    lines = []

    # Header
    lines.append(f"# Handoff: {data['name']}")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Mode:** Smart (AI-summarized)")
    lines.append(f"**Original Chat Period:** {data['created']} to {data['updated']}")
    lines.append(f"**Message Count:** {len(messages)} ({len(older_messages)} summarized, {len(recent_messages)} verbatim)")
    lines.append(f"**Original Size:** ~{estimate_tokens(total_chars):,} tokens")
    lines.append("")

    # Claude.ai's built-in summary (if available) - this is gold from JSON exports
    if data['summary']:
        lines.append("## Quick Context (Claude.ai Auto-generated)")
        lines.append("")
        lines.append(data['summary'])
        lines.append("")

    # AI Summary
    if ai_summary:
        lines.append("## AI-Summarized Earlier Content")
        lines.append("")
        lines.append(ai_summary)
        lines.append("")

    # Recent messages verbatim
    lines.append("## Recent Conversation (Verbatim)")
    lines.append("")
    lines.append(f"*Last {len(recent_messages)} messages preserved for immediate context:*")
    lines.append("")

    for msg in recent_messages:
        sender_label = "**Human:**" if msg['sender'] == 'human' else "**Assistant:**"
        lines.append(f"### {sender_label} ({msg['timestamp']})")
        lines.append("")
        content = msg['content']
        if len(content) > 8000:
            content = content[:8000] + f"\n\n*[Truncated - original was {len(msg['content']):,} chars]*"
        lines.append(content)
        lines.append("")

    # Footer
    lines.append("---")
    lines.append("")
    lines.append("## Resumption Instructions")
    lines.append("")
    lines.append("1. Share this handoff document with Claude in a new chat")
    lines.append("2. Reference any relevant project documentation")
    lines.append("3. Specify which aspect of the work to continue")
    lines.append("")

    return '\n'.join(lines)


# ============================================================================
# INTERACTIVE UI
# ============================================================================

def print_banner():
    print()
    print("=" * 60)
    print("  CLAUDE.AI CHAT HANDOFF GENERATOR")
    print("  Recover context from stuck chat sessions")
    print("=" * 60)
    print()
    print("  Input:  JSON exports only (complete data, built-in summary)")
    print("  Output: Compressed Markdown handoff (~90% smaller)")
    print()


def list_available_exports():
    """List JSON files in default input directory"""
    if not DEFAULT_INPUT_DIR.exists():
        return []

    # JSON only - research shows MD exports are lossy
    files = list(DEFAULT_INPUT_DIR.glob('*.json'))

    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def get_input_file():
    """Prompt user for input file"""
    available = list_available_exports()

    print(f"Input directory: {DEFAULT_INPUT_DIR}")
    print()

    if available:
        print("Available JSON exports (most recent first):")
        for i, f in enumerate(available[:10], 1):
            size_kb = f.stat().st_size // 1024
            print(f"  [{i}] {f.name} ({size_kb} KB)")
        print()
        print("Enter number, filename, or full path:")
    else:
        print("No JSON exports found in default directory.")
        print()
        print("  TIP: Export your Claude.ai chat as JSON (not Markdown)")
        print("       JSON exports include the built-in summary field")
        print("       and preserve complete conversation data.")
        print()
        print("  See README for browser extension recommendations.")
        print()
        print("Enter filename or full path to JSON file:")

    user_input = input("> ").strip()

    if not user_input:
        print("No input provided. Exiting.")
        sys.exit(0)

    # Check if number selection
    if user_input.isdigit():
        idx = int(user_input) - 1
        if 0 <= idx < len(available):
            return available[idx]
        else:
            print(f"Invalid selection: {user_input}")
            sys.exit(1)

    # Check if full path
    if os.path.isabs(user_input) or ':' in user_input:
        path = Path(user_input)
    else:
        # Assume filename in default directory
        path = DEFAULT_INPUT_DIR / user_input

    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    # Validate JSON format
    if path.suffix.lower() != '.json':
        print()
        print("  ⚠ WARNING: This tool is optimized for JSON exports.")
        print("    Markdown exports are lossy (missing summary, branch data, model info).")
        print("    Please re-export your chat as JSON for best results.")
        print()
        confirm = input("Continue anyway? [y/N]: ").strip().lower()
        if confirm != 'y':
            sys.exit(0)

    return path


def get_mode(total_tokens, config):
    """Prompt user for processing mode - Smart is default"""
    print()
    print("-" * 60)
    print("Processing Mode:")
    print()
    print("  [1] Smart    - AI-powered summarization (DEFAULT)")
    print("                 ~90% compression, structured output")
    print("  [2] Standard - Algorithmic extraction (offline)")
    print("                 ~50% compression, basic extraction")
    print()

    # Check if API key is configured
    api_key = config.get('anthropic_api_key', '')
    has_key = api_key and api_key not in ['YOUR_API_KEY_HERE', '']

    if not has_key:
        print("  ⚠ Smart mode requires API key in config.json")
        print("    Copy config.example.json to config.json and add your key.")
        print("    Defaulting to Standard mode.")
        return 'standard'

    print(f"  Chat size: ~{total_tokens:,} tokens")
    print()
    user_input = input("Select mode [1]: ").strip()

    if user_input == '2':
        return 'standard'
    return 'smart'


def main():
    print_banner()

    # Load config
    config = load_config()

    # Ensure directories exist
    DEFAULT_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Get input file
    input_path = get_input_file()
    print(f"\nSelected: {input_path.name}")

    # Parse JSON
    print("\nParsing JSON export...")
    try:
        data = parse_json_export(input_path)
    except ValueError as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    total_chars = sum(len(m['content']) for m in data['messages'])
    total_tokens = estimate_tokens(total_chars)

    print(f"  Chat name: {data['name']}")
    print(f"  Messages: {len(data['messages'])} (after noise filtering)")
    print(f"  Estimated tokens: ~{total_tokens:,}")
    if data['summary']:
        print(f"  Claude.ai summary: ✓ Available")
    else:
        print(f"  Claude.ai summary: Not available")

    # Get mode
    mode = get_mode(total_tokens, config)

    # Generate handoff
    print()
    print("Generating handoff...")

    if mode == 'smart':
        api_key = config.get('anthropic_api_key', '')
        handoff_content = generate_handoff_smart(data, api_key, config)
    else:
        handoff_content = generate_handoff_standard(data)

    # Create output filename
    safe_name = re.sub(r'[^\w\s\-]', '', data['name'])
    safe_name = re.sub(r'\s+', '-', safe_name).lower()[:50]
    date_str = datetime.now().strftime('%Y-%m-%d')
    mode_suffix = "-smart" if mode == 'smart' else "-standard"
    output_filename = f"{date_str}-handoff-{safe_name}{mode_suffix}.md"

    output_path = DEFAULT_OUTPUT_DIR / output_filename

    # Save
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(handoff_content)

    # Calculate compression ratio
    input_size = input_path.stat().st_size
    output_size = len(handoff_content.encode('utf-8'))
    compression = (1 - output_size / input_size) * 100 if input_size > 0 else 0

    # Done
    print()
    print("=" * 60)
    print("  DONE!")
    print("=" * 60)
    print(f"  Mode:        {mode.upper()}")
    print(f"  Input:       {input_size // 1024} KB (JSON)")
    print(f"  Output:      {output_size // 1024} KB (MD)")
    print(f"  Compression: {compression:.0f}%")
    print(f"  Saved:       {output_filename}")
    print(f"  Path:        {output_path}")
    print()
    print("Use this handoff to bootstrap a new Claude session.")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled.")
    except Exception as e:
        print(f"\nERROR: {e}")
    finally:
        input("\nPress Enter to exit...")
