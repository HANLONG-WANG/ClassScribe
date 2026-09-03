# nemotron worker

Nemotron speech adapter. This process owns its model dependencies and must communicate with core only
through the versioned Unix-domain-socket worker protocol. Stage 1 supplies a dependency-light
\`--health-check\`; model loading and MessagePack request handling are added in the stage that owns
this model family.

The core project must never import this directory or its future deep-learning dependencies.
