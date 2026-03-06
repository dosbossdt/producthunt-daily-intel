#!/usr/bin/env python3
"""
ProductHunt Daily Intel - Automated Product Analysis
"""

import os
import json
import time
import re
import anthropic
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import requests
import tempfile

# Configuration
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
GOOGLE_DRIVE_FOLDER_ID = "0ANSceNat0SgkUk9PVA"
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]

# Retry configuration
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 60  # Start with 60 seconds

# Continuation configuration
MAX_CONTINUATIONS = 3  # Max times to continue a truncated response

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are an expert product analyst and product architect. Your job is to reverse-engineer a successful SaaS product from ProductHunt, deeply understand its users' pain points, and then produce a complete product specification for building an improved clone of that product.

The clone should replicate the core functionality of the original product but be designed from the ground up to address unmet user needs surfaced in ProductHunt comments and reviews.

IMPORTANT OUTPUT FORMAT RULES:
1. Do NOT include your thinking process, reasoning, or search narration in your final output
2. Do NOT write things like "Let me search for..." or "I found that..."
3. Your ONLY output should be the final product specification document in clean Markdown
4. Start your output directly with the H1 title: # Clone of [Original Product Name] - Product Specification

Your output must be a complete product specification in Markdown with these 12 sections:

# Clone of [Original Product Name] - Product Specification

## 1. Executive Summary
- **Clone Product Name:** Clone of [Original Product Name]
- **Original Product:** [name and one-liner description of the original]
- **Original Product URL:** [url]
- **Original ProductHunt URL:** [url]
- **Target User:** [who this clone is for — same audience as original]
- **Core Value Proposition:** [what the original does well that this clone replicates]
- **Clone Differentiator:** [1-2 sentence summary of the key enhancement this clone adds over the original, derived from user pain points]
- **Analysis Date:** [date]

## 2. Original Product Analysis
[Thorough breakdown of the original product: what problem it solves, how it works, its core features, its strengths, and its weaknesses. This section sets the foundation — you are deconstructing the original so it can be faithfully rebuilt and improved.]

## 3. User Personas & Jobs-to-be-Done
[Personas and JTBD derived from the original product's user base. These should reflect the real users commenting on ProductHunt and using the original product.]

## 4. Feature Specification
[Core features the clone must replicate from the original product. For each feature include user stories, inputs/outputs, and business rules. This should cover everything needed to reach feature parity with the original.]

## 5. Technical Architecture
[Recommended tech stack with rationale, data model, API endpoints. This is for the clone — design it as a greenfield build informed by what the original does.]

## 6. User Flows
[Critical user journeys the clone must support, mirroring the original product's key workflows. Include success and error states.]

## 7. UI/UX Specification
[Key screens the clone needs, design system notes. Reference the original product's UX patterns where appropriate — note what works well and should be kept, and what could be improved.]

## 8. Non-Functional Requirements
[Performance, security, scalability, accessibility targets for the clone.]

## 9. Implementation Roadmap (Claude Code Optimized)
This roadmap is specifically designed for AI agent implementation (Claude Code). Each phase MUST be:
- Completable in a SINGLE Claude Code session (under 100k tokens of context to maintain code quality and accuracy)
- Small, atomic, and independently testable
- Specific enough that an AI agent can execute without ambiguity

Format each phase as:
### Phase X: [Short Title]
**Scope:** [1-2 sentence description]
**Files to create/modify:** [specific file paths]
**Acceptance criteria:** [bullet list of specific, testable outcomes]
**Estimated complexity:** [Low/Medium - never High, break down further if High]

Break the implementation into 10-20 small phases. Err on the side of MORE phases with LESS scope each.
Example phase sizes: "Set up project structure and dependencies", "Create user data model", "Build login API endpoint", "Add input validation to login", "Create login form component", "Connect login form to API", "Add error handling to login flow"

IMPORTANT: The final phases of the roadmap MUST implement the Enhancement from Section 12 (the pain point solution). After completing the core clone phases, add as many additional phases as needed to fully implement the enhancement feature. Apply the same sizing rules — each phase must be completable within 100k tokens of context. Label these phases clearly as "Enhancement Phase X" so they are distinguishable from core clone phases.

## 10. Open Questions & Assumptions
[Questions and assumptions relevant to building the clone.]

## 11. Competitive Context
[How the original product sits in its competitive landscape. Include a competitor comparison table showing the original product vs 2-3 competitors. Note where the clone, with its enhancement, would be positioned.]

## 12. Enhancement: Pain Point Solution
[This is the clone's key differentiator. From ProductHunt comments and reviews of the original product, identify the highest-priority UNADDRESSED pain point. Include:
- Direct source quotes from ProductHunt comments showing the pain point
- Frequency/severity assessment
- Proposed feature that addresses this pain point (with user story)
- Technical approach for implementing it
- UI/UX changes needed
This enhancement is what justifies the clone's existence — it solves a real problem the original product has not addressed.]

Be comprehensive enough that someone could build the clone from your spec."""

USER_PROMPT_TEMPLATE = """Today's date is {date}.

Your task: Find today's #1 Product of the Day on ProductHunt, reverse-engineer it, and write a complete product specification for building an improved CLONE of that product.

## Instructions

1. **Find Product of the Day**: Search ProductHunt for today's #1 Product of the Day (or yesterday's if today's winner hasn't been announced — winners are announced ~3pm PT / 11pm GMT). If #1 has insufficient info, use #2, then #3, and so on.

2. **Research the original product thoroughly**:
   - Product's official website (features, pricing, how it works)
   - ProductHunt page and ALL comments
   - 2-3 direct competitors
   - Tech blogs, reviews, job postings (for tech stack signals)

3. **Analyze pain points** from ProductHunt comments on the original product:
   - Feature requests
   - Complaints and concerns
   - "I wish it could..." statements
   - Workarounds users mention
   - Select the highest-priority UNADDRESSED pain point — this becomes the clone's key differentiator

4. **Generate the full 12-section specification** for the clone:
   - The clone is called "Clone of [Original Product Name]"
   - The clone replicates the original product's core functionality
   - The clone adds an enhancement (Section 12) that addresses the top user pain point
   - Reference the original product by name throughout, link to its URLs, and explain what you are replicating and why

CRITICAL: Your response must ONLY contain the final Markdown specification document.
- Start directly with: # Clone of [Original Product Name] - Product Specification
- Do NOT include any thinking, reasoning, or search narration
- Output ONLY the clean, formatted specification document"""


def call_claude_with_retry(messages: list, system: str, max_tokens: int = 64000) -> anthropic.types.Message:
    """Call Claude API with automatic retry on rate limit errors."""
    for attempt in range(MAX_RETRIES):
        try:
            return client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                system=system,
                messages=messages
            )
        except anthropic.RateLimitError as e:
            if attempt == MAX_RETRIES - 1:
                raise  # Re-raise on final attempt
            delay = INITIAL_RETRY_DELAY * (2 ** attempt)  # Exponential backoff: 60s, 120s, 240s, 480s
            print(f"  Rate limited. Waiting {delay} seconds before retry {attempt + 2}/{MAX_RETRIES}...")
            time.sleep(delay)
    raise Exception("Max retries exceeded")


def extract_product_info(spec_content: str) -> tuple[str, str]:
    """Extract product name and URL from the specification content."""
    product_name = "Unknown Product"
    product_url = ""

    # Try multiple patterns to find the product name
    # Pattern 1: # Clone of Product Name - Product Specification
    title_match = re.search(r'^#\s+Clone of\s+(.+?)\s*-\s*Product Specification', spec_content, re.MULTILINE)
    if title_match:
        product_name = f"Clone of {title_match.group(1).strip()}"

    # Pattern 2: **Clone Product Name:** value
    if product_name == "Unknown Product":
        name_match = re.search(r'\*\*Clone Product Name:\*\*\s*(.+?)(?:\n|$)', spec_content)
        if name_match:
            product_name = name_match.group(1).strip()

    # Pattern 3: **Original Product:** value (fallback — prefix with "Clone of")
    if product_name == "Unknown Product":
        name_match = re.search(r'\*\*Original Product:\*\*\s*(.+?)(?:\n|$)', spec_content)
        if name_match:
            original = name_match.group(1).strip()
            product_name = f"Clone of {original}"

    # Pattern 4: First H1 heading
    if product_name == "Unknown Product":
        h1_match = re.search(r'^#\s+(.+?)(?:\n|$)', spec_content, re.MULTILINE)
        if h1_match:
            product_name = h1_match.group(1).strip()
            product_name = re.sub(r'\s*-\s*Product Specification.*$', '', product_name)

    # Extract original product URL
    url_match = re.search(r'\*\*Original Product URL:\*\*\s*(https?://[^\s\n]+)', spec_content)
    if url_match:
        product_url = url_match.group(1).strip()

    return product_name, product_url


def clean_spec_content(content: str) -> str:
    """Remove any thinking/reasoning text and keep only the specification."""
    # Find where the actual spec starts (first H1 heading)
    spec_start = re.search(r'^#\s+.+?(?:Product Specification|Specification)', content, re.MULTILINE | re.IGNORECASE)

    if spec_start:
        return content[spec_start.start():]

    # If no clear spec header, try to find the first H1
    h1_match = re.search(r'^#\s+', content, re.MULTILINE)
    if h1_match:
        return content[h1_match.start():]

    # Return as-is if we can't find a clear starting point
    return content


def get_analyzed_products() -> list[str]:
    """Fetch list of already-analyzed product names from Google Drive."""
    print("Checking previously analyzed products...")

    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    drive_service = build("drive", "v3", credentials=credentials)

    analyzed_products = []
    page_token = None

    while True:
        response = drive_service.files().list(
            q=f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken, files(name)",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        for file in response.get("files", []):
            # Extract product name from filename format: "YYYY-MM-DD - Clone of Product Name"
            match = re.match(r'^\d{4}-\d{2}-\d{2}\s*-\s*(.+)$', file["name"])
            if match:
                name = match.group(1).strip()
                # Strip "Clone of " prefix for exclusion matching against original names
                original_name = re.sub(r'^Clone of\s+', '', name, flags=re.IGNORECASE)
                analyzed_products.append(original_name)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    print(f"Found {len(analyzed_products)} previously analyzed products")
    return analyzed_products


def run_analysis(excluded_products: list[str]) -> tuple[str, str, str]:
    """Run the ProductHunt analysis using Claude with web search."""
    today = datetime.now().strftime("%A, %B %d, %Y")
    print(f"Starting ProductHunt analysis for {today}...")

    # Build the exclusion clause for the prompt
    exclusion_text = ""
    if excluded_products:
        exclusion_text = f"""

## Previously Analyzed Products (DO NOT ANALYZE THESE)
The following products have already been analyzed. Skip them and choose the next highest-ranked product instead (keep going down the list until you find one not on this list):
{chr(10).join(f'- {name}' for name in excluded_products)}
"""

    user_prompt = USER_PROMPT_TEMPLATE.format(date=today) + exclusion_text
    messages = [{"role": "user", "content": user_prompt}]

    response = call_claude_with_retry(messages, SYSTEM_PROMPT)
    print(f"  Response stop_reason: {response.stop_reason}")

    while response.stop_reason == "tool_use":
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"  Searching: {block.input.get('query', 'N/A')}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": "Search completed"
                })
        messages.append({"role": "user", "content": tool_results})
        response = call_claude_with_retry(messages, SYSTEM_PROMPT)
        print(f"  Response stop_reason: {response.stop_reason}")

    # Collect all text content from the response
    raw_content = "".join(block.text for block in response.content if hasattr(block, "text"))

    # Handle truncated responses — if stop_reason is max_tokens, continue generation
    continuation_count = 0
    while response.stop_reason == "max_tokens" and continuation_count < MAX_CONTINUATIONS:
        continuation_count += 1
        print(f"  Response was truncated (max_tokens). Continuing generation ({continuation_count}/{MAX_CONTINUATIONS})...")

        # Append the partial assistant response and ask Claude to continue
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": "Continue exactly where you left off. Do not repeat any content."})

        response = call_claude_with_retry(messages, SYSTEM_PROMPT)
        print(f"  Continuation stop_reason: {response.stop_reason}")

        # Append the new text content
        continuation_text = "".join(block.text for block in response.content if hasattr(block, "text"))
        raw_content += continuation_text

    if continuation_count > 0:
        print(f"  Completed after {continuation_count} continuation(s)")

    # Log content size for debugging
    print(f"  Raw content length: {len(raw_content)} chars")

    # Validate that we have meaningful content
    if not raw_content or len(raw_content.strip()) < 500:
        raise ValueError(
            f"Analysis produced insufficient content ({len(raw_content.strip())} chars). "
            f"stop_reason was '{response.stop_reason}'. "
            f"This likely means the model ran out of output tokens before generating the specification. "
            f"Response had {len(response.content)} content blocks."
        )

    # Clean the content to remove any thinking/reasoning
    spec_content = clean_spec_content(raw_content)

    # Extract product info using improved parsing
    product_name, product_url = extract_product_info(spec_content)

    # Validate product name was extracted
    if product_name == "Unknown Product":
        print(f"  WARNING: Could not extract product name from spec. First 200 chars: {spec_content[:200]}")

    print(f"Analysis complete for: {product_name}")
    return product_name, spec_content, product_url


def upload_to_drive(product_name: str, spec_content: str) -> str:
    """Upload the spec to Google Drive as a Google Doc in a Shared Drive."""
    print("Uploading to Google Drive...")

    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    drive_service = build("drive", "v3", credentials=credentials)

    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"{today} - {product_name}"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(spec_content)
        temp_path = f.name

    try:
        file_metadata = {
            "name": filename,
            "parents": [GOOGLE_DRIVE_FOLDER_ID],
            "mimeType": "application/vnd.google-apps.document"
        }
        media = MediaFileUpload(temp_path, mimetype="text/markdown", resumable=True)
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True
        ).execute()

        doc_url = file.get("webViewLink", f"https://docs.google.com/document/d/{file['id']}")
        print(f"Uploaded: {doc_url}")
        return doc_url
    finally:
        os.unlink(temp_path)


def send_slack_notification(success: bool, product_name: str = "", doc_url: str = "", product_url: str = "", error_message: str = ""):
    """Send Slack notification."""
    if success:
        payload = {
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "✅ ProductHunt Daily Intel Complete", "emoji": True}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Product:*\n{product_name}"},
                    {"type": "mrkdwn", "text": f"*Date:*\n{datetime.now().strftime('%Y-%m-%d')}"}
                ]},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"📄 <{doc_url}|View Specification>"}}
            ]
        }
        if product_url:
            payload["blocks"].append({"type": "section", "text": {"type": "mrkdwn", "text": f"🔗 <{product_url}|Visit Original Product>"}})
    else:
        payload = {
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": "❌ ProductHunt Daily Intel Failed", "emoji": True}},
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Error:*\n```{error_message[:500]}```"}}
            ]
        }

    try:
        requests.post(SLACK_WEBHOOK_URL, json=payload, headers={"Content-Type": "application/json"}).raise_for_status()
        print("Slack notification sent")
    except Exception as e:
        print(f"Slack notification failed: {e}")


def main():
    try:
        excluded_products = get_analyzed_products()
        product_name, spec_content, product_url = run_analysis(excluded_products)
        doc_url = upload_to_drive(product_name, spec_content)
        send_slack_notification(True, product_name, doc_url, product_url)
        print("Daily intel complete!")
    except Exception as e:
        send_slack_notification(False, error_message=str(e))
        raise


if __name__ == "__main__":
    main()
