# DentalClarityApp

DentalClarityApp is a human-reviewed monitoring tool designed to identify public dental questions on Reddit where verified educational information may be helpful.

## Purpose

The application is being developed to monitor a limited set of dental-related communities and identify questions that may correspond to educational resources available through DentalClarity.org.

DentalClarity is a free dental education resource intended to help patients better understand dental procedures. It does not provide diagnoses and is not a substitute for professional dental care.

## How It Works

1. Public Reddit posts will be retrieved through approved Reddit API access.
2. A local relevance filter identifies potentially relevant dental questions.
3. Relevant posts may be analyzed to determine whether they correspond to a verified procedure in the DentalClarity knowledge base.
4. Potential matches and draft educational responses are displayed in a private dashboard for human review.
5. If no verified DentalClarity resource exists for a topic, the system does not create or invent one.

## Current Reddit Integration

The initial Reddit integration is **MONITOR-ONLY**. Reddit API access is not included in this repository yet and will only be enabled after approval and credential configuration.

The application does not automatically:

- Post comments or create posts
- Vote
- Send private messages or chats
- Contact Reddit users
- Perform moderation actions

Potential responses are reviewed by a human before any interaction with Reddit.

## Safety and Transparency

DentalClarityApp uses verified local educational content, conservative relevance filtering, human oversight, usage limits, and transparent disclosure of affiliation when DentalClarity resources are referenced. The application is not intended to diagnose dental conditions or replace evaluation by a licensed dental professional.

## Repository Safety

Secrets and runtime data are intentionally excluded from version control. `.env` files, API credentials, AI usage history, generated analysis results, and local databases must never be committed. `.env.example` contains placeholders only.

## Local Development

Create a local `.env` from `.env.example`, add your own API credentials locally, install `requirements.txt`, and run the FastAPI application with Uvicorn. Do not commit the resulting `.env` or runtime JSON files.

## Website

DentalClarity.org
