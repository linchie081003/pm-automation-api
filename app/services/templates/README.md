# Organization templates

Place files listed in `backend/templates/manifest.json`. Override folder with env `TEMPLATE_DIR`.

## Placeholder tokens (search/replace in generators)

| Token | Source |
|-------|--------|
| `{{PROJECT_NAME}}` | Project.name |
| `{{PROJECT_CODE}}` | Project.code |
| `{{CLIENT_NAME}}` | Project.client_name |
| `{{SCOPE}}` | SPH / Pre-KO scope |
| `{{NON_SCOPE}}` | SPH non-scope |
| `{{PO_START}}` | po_date |
| `{{PO_DUE}}` | po_due_date |
| `{{MILESTONE_TABLE}}` | newline-separated milestone lines |

PPTX: replace in all shapes text frames. XLSX: replace in sheet cells containing tokens.
