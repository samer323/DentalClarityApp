# DentalClarityApp
A human-reviewed Reddit monitoring tool that identifies dental questions where verified DentalClarity educational resources may be relevant.

## Purpose

The application monitors a limited set of dental-related communities and identifies questions that may correspond to educational resources available through DentalClarity.org.

DentalClarity is a free dental education resource intended to help patients better understand dental procedures. It does not provide diagnoses and is not a substitute for professional dental care.

## How It Works

1. Public Reddit posts are retrieved through the approved Reddit API.
2. A local relevance filter identifies potentially relevant dental questions.
3. Relevant posts may be analyzed to determine whether they correspond to a verified procedure in the DentalClarity knowledge base.
4. Potential matches and draft educational responses are displayed in a private dashboard for human review.
5. If no verified DentalClarity resource exists for a topic, the system does not create or invent one.

## Current Reddit Integration

The initial Reddit integration is MONITOR-ONLY.

The application does not automatically:

- Post comments
- Create posts
- Vote
- Send private messages or chats
- Contact Reddit users
- Perform moderation actions

Potential responses are reviewed by a human before any interaction with Reddit.

## Safety and Transparency

DentalClarityApp is designed around verified local educational content, conservative relevance filtering, human oversight, usage limits, and transparent disclosure of affiliation when DentalClarity resources are referenced.

The application is not intended to diagnose dental conditions or replace evaluation by a licensed dental professional.

## Website

DentalClarity.org
