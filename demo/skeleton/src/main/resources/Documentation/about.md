Minimal Gerrit plugin used by the gerrit-stack demo. It exposes one REST view,
`GET /projects/{name}/demo-plugin~ping`, and one SSH command,
`demo-plugin ping <project>`, both answering with the configured ping message.
