# Exact API and repository inventory

Source: tracked working-tree files, 9 October 2026. Demo password redacted in export. Signatures below are statically extracted, not proof the server imports or starts successfully.

## MCP tools

### `health_check`

- Source: `backend/mcp_server.py:88`
- Signature: `health_check() -> dict`
- Purpose: Returns server status. Used to verify MCP connection.

### `full_aws_scan`

- Source: `backend/mcp_server.py:94`
- Signature: `full_aws_scan(region: str='us-east-1') -> dict`
- Purpose: Run all five AWS service scans and return combined infrastructure data.

### `scan_ec2_instances`

- Source: `backend/mcp_server.py:106`
- Signature: `scan_ec2_instances(region: str='us-east-1') -> dict`
- Purpose: Scan all EC2 instances in the specified AWS region.

### `scan_s3_buckets`

- Source: `backend/mcp_server.py:112`
- Signature: `scan_s3_buckets() -> dict`
- Purpose: Scan all S3 buckets and check public-access status.

### `scan_iam_users`

- Source: `backend/mcp_server.py:118`
- Signature: `scan_iam_users() -> dict`
- Purpose: Scan all IAM users and check MFA status and last-login date.

### `scan_security_groups_detail`

- Source: `backend/mcp_server.py:124`
- Signature: `scan_security_groups_detail(region: str='us-east-1') -> dict`
- Purpose: Scan all EC2 security groups and flag dangerous internet-facing rules.

### `scan_vpc_detail`

- Source: `backend/mcp_server.py:130`
- Signature: `scan_vpc_detail(region: str='us-east-1') -> dict`
- Purpose: Scan all VPCs including subnet counts.

### `scan_sg_usage_tool`

- Source: `backend/mcp_server.py:136`
- Signature: `scan_sg_usage_tool(region: str='us-east-1', aws_access_key_id: str='', aws_secret_access_key: str='') -> dict`
- Purpose: Map every security group to the resources currently attached to it.

### `analyse_security_findings`

- Source: `backend/mcp_server.py:152`
- Signature: `analyse_security_findings(scan_data: dict) -> dict`
- Purpose: Run 7 built-in security rules against raw AWS scan data.

### `run_security_analysis_with_summary`

- Source: `backend/mcp_server.py:163`
- Signature: `async run_security_analysis_with_summary(region: str='us-east-1', model: str='groq', api_key: str='', ollama_model_name: str=DEFAULT_MODEL) -> dict`
- Purpose: Scan AWS, run security rules, and generate an LLM plain-English summary.

### `estimate_costs`

- Source: `backend/mcp_server.py:214`
- Signature: `estimate_costs(region: str='us-east-1', time_period_days: int=30) -> dict`
- Purpose: Retrieve AWS spend data and detect cost anomalies via Cost Explorer.

### `get_cost_with_summary`

- Source: `backend/mcp_server.py:228`
- Signature: `async get_cost_with_summary(region: str='us-east-1', model: str='groq', api_key: str='', ollama_model_name: str=DEFAULT_MODEL) -> dict`
- Purpose: Retrieve AWS cost data and generate an LLM cost-optimisation summary.

### `generate_terraform_hcl`

- Source: `backend/mcp_server.py:264`
- Signature: `async generate_terraform_hcl(resource_type: str, config: dict) -> dict`
- Purpose: Generate Terraform HCL from a resource type and config dict.

### `generate_terraform_from_request`

- Source: `backend/mcp_server.py:286`
- Signature: `async generate_terraform_from_request(request: str, model: str='groq', api_key: str='', aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1') -> dict`
- Purpose: Generate Terraform HCL from a plain-English request string.

### `validate_terraform_plan`

- Source: `backend/mcp_server.py:349`
- Signature: `validate_terraform_plan(hcl: str) -> dict`
- Purpose: Validate Terraform HCL syntax without touching AWS.

### `run_terraform_plan_mcp`

- Source: `backend/mcp_server.py:372`
- Signature: `async run_terraform_plan_mcp(hcl_config: str, description: str='', aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1') -> dict`
- Purpose: Write HCL to a persistent working directory, run terraform init + plan.

### `run_terraform_apply_mcp`

- Source: `backend/mcp_server.py:413`
- Signature: `run_terraform_apply_mcp(execution_id: str, approved: bool, aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1') -> dict`
- Purpose: Apply or reject a previously planned Terraform execution.

### `get_execution_history_tool`

- Source: `backend/mcp_server.py:453`
- Signature: `get_execution_history_tool() -> dict`
- Purpose: Return all past Terraform plan/apply executions from the execution log.

### `summarise_plan_for_human`

- Source: `backend/mcp_server.py:459`
- Signature: `summarise_plan_for_human(plan_output: str, issue_being_fixed: str, risk_level: str='medium') -> dict`
- Purpose: Parse raw terraform plan output and produce a plain-English approval summary.

### `aws_chat`

- Source: `backend/mcp_server.py:469`
- Signature: `async aws_chat(message: str, model: str='groq', api_key: str='', history: list=None, region: str='us-east-1', ollama_model_name: str=DEFAULT_MODEL) -> dict`
- Purpose: Chat with an LLM about your live AWS infrastructure.

### `agent_run`

- Source: `backend/mcp_server.py:530`
- Signature: `async agent_run(region: str='us-east-1', model: str='groq', api_key: str='', aws_access_key_id: str='', aws_secret_access_key: str='', ollama_model_name: str=DEFAULT_MODEL) -> dict`
- Purpose: Autonomous security remediation agent — scan, pick top issue, generate fix, plan.

### `agent_approve`

- Source: `backend/mcp_server.py:688`
- Signature: `agent_approve(execution_id: str, approved: bool, aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1') -> dict`
- Purpose: Approve or reject an agent-generated fix plan.

### `revoke_open_ingress_rule`

- Source: `backend/mcp_server.py:762`
- Signature: `revoke_open_ingress_rule(sg_id: str, port: int, region: str='us-east-1', aws_access_key_id: str='', aws_secret_access_key: str='') -> dict`
- Purpose: Revoke all 0.0.0.0/0 and ::/0 ingress rules for a specific port on a security group.

### `mark_execution_resolved`

- Source: `backend/mcp_server.py:780`
- Signature: `mark_execution_resolved(execution_id: str) -> dict`
- Purpose: Mark a security finding execution as manually resolved in the execution log.

### `rollback_execution`

- Source: `backend/mcp_server.py:790`
- Signature: `async rollback_execution(execution_id: str, aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1') -> dict`
- Purpose: Destroy resources created by a previous terraform apply (rollback).

### `rag_query_tool`

- Source: `backend/mcp_server.py:827`
- Signature: `async rag_query_tool(question: str, n_results: int=3, resource_type: str='', model: str='groq', api_key: str='', groq_key: str='') -> dict`
- Purpose: Search the security knowledge base and answer with LLM grounding.

### `rag_list_documents`

- Source: `backend/mcp_server.py:862`
- Signature: `rag_list_documents() -> dict`
- Purpose: List all documents currently stored in the ChromaDB knowledge base.

### `rag_add_text_document`

- Source: `backend/mcp_server.py:886`
- Signature: `rag_add_text_document(doc_id: str, text: str, resource_type: str='general') -> dict`
- Purpose: Add a text document to the ChromaDB knowledge base.

### `rag_delete_document`

- Source: `backend/mcp_server.py:904`
- Signature: `rag_delete_document(doc_id: str) -> dict`
- Purpose: Delete a document and all its chunks from the knowledge base.

### `rag_upload_file`

- Source: `backend/mcp_server.py:925`
- Signature: `rag_upload_file(doc_id: str, file_content_base64: str, filename: str, resource_type: str='general') -> dict`
- Purpose: Add a file (PDF or plain text) to the knowledge base from base64-encoded content.

### `ollama_status`

- Source: `backend/mcp_server.py:970`
- Signature: `async ollama_status(base_url: str='http://localhost:11434') -> dict`
- Purpose: Probe a local Ollama instance and list installed models.

## MCP resources

- `mcp.resource('aws://findings/{region}')` → `aws_findings_resource(region: str)`; source line 976.
- `mcp.resource('aws://cost-summary/{region}')` → `aws_cost_summary_resource(region: str)`; source line 1002.

## Additional HTTP surfaces

FastAPI main: GET /health; GET /terraform/keys/{execution_id}/{filename}; POST /rag/documents/upload; generated POST /tools/{tool_name}; /docs, /redoc, /openapi.json; MCP mounted /mcp (frontend dev defaults /mcp/).

Standalone mcp_server.create_app: MCP /mcp; POST /rag/documents/upload; GET /api/ollama/status; POST /api/ollama/pull. These are not identical to main:app.

## Python module/function inventory

### `backend/benchmark.py`

- `_call(fn)` — line 24.
- `benchmark(label: str, fn, runs: int=RUNS)` — line 32.
- `run_all_benchmarks()` — line 94.
- `print_summary(results: list)` — line 179.
- `save_results(results: list, path: str='benchmark_results.json')` — line 201.

### `backend/docs_generator.py`

- `_build_input_model(tool_name: str, schema: dict)` — line 66.
- `_make_handler(tool_name: str, mcp_instance, input_model)` — line 109.
- `register_mcp_docs_routes(app: FastAPI, mcp_instance)` — line 166.

### `backend/main.py`

- `health()` — line 42.
- `download_key_file(execution_id: str, filename: str)` — line 51.
- `upload_rag_document(file: UploadFile=File(...), doc_id: str=Form(...), resource_type: str=Form('general'))` — line 84.

### `backend/mcp_server.py`

- `_resolve_key(model: str, override: str)` — line 72.
- `health_check()` — line 88.
- `full_aws_scan(region: str='us-east-1')` — line 94.
- `scan_ec2_instances(region: str='us-east-1')` — line 106.
- `scan_s3_buckets()` — line 112.
- `scan_iam_users()` — line 118.
- `scan_security_groups_detail(region: str='us-east-1')` — line 124.
- `scan_vpc_detail(region: str='us-east-1')` — line 130.
- `scan_sg_usage_tool(region: str='us-east-1', aws_access_key_id: str='', aws_secret_access_key: str='')` — line 136.
- `analyse_security_findings(scan_data: dict)` — line 152.
- `run_security_analysis_with_summary(region: str='us-east-1', model: str='groq', api_key: str='', ollama_model_name: str=DEFAULT_MODEL)` — line 163.
- `estimate_costs(region: str='us-east-1', time_period_days: int=30)` — line 214.
- `get_cost_with_summary(region: str='us-east-1', model: str='groq', api_key: str='', ollama_model_name: str=DEFAULT_MODEL)` — line 228.
- `generate_terraform_hcl(resource_type: str, config: dict)` — line 264.
- `generate_terraform_from_request(request: str, model: str='groq', api_key: str='', aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1')` — line 286.
- `validate_terraform_plan(hcl: str)` — line 349.
- `run_terraform_plan_mcp(hcl_config: str, description: str='', aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1')` — line 372.
- `run_terraform_apply_mcp(execution_id: str, approved: bool, aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1')` — line 413.
- `get_execution_history_tool()` — line 453.
- `summarise_plan_for_human(plan_output: str, issue_being_fixed: str, risk_level: str='medium')` — line 459.
- `aws_chat(message: str, model: str='groq', api_key: str='', history: list=None, region: str='us-east-1', ollama_model_name: str=DEFAULT_MODEL)` — line 469.
- `agent_run(region: str='us-east-1', model: str='groq', api_key: str='', aws_access_key_id: str='', aws_secret_access_key: str='', ollama_model_name: str=DEFAULT_MODEL)` — line 530.
- `agent_approve(execution_id: str, approved: bool, aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1')` — line 688.
- `revoke_open_ingress_rule(sg_id: str, port: int, region: str='us-east-1', aws_access_key_id: str='', aws_secret_access_key: str='')` — line 762.
- `mark_execution_resolved(execution_id: str)` — line 780.
- `rollback_execution(execution_id: str, aws_access_key_id: str='', aws_secret_access_key: str='', aws_region: str='us-east-1')` — line 790.
- `rag_query_tool(question: str, n_results: int=3, resource_type: str='', model: str='groq', api_key: str='', groq_key: str='')` — line 827.
- `rag_list_documents()` — line 862.
- `rag_add_text_document(doc_id: str, text: str, resource_type: str='general')` — line 886.
- `rag_delete_document(doc_id: str)` — line 904.
- `rag_upload_file(doc_id: str, file_content_base64: str, filename: str, resource_type: str='general')` — line 925.
- `ollama_status(base_url: str='http://localhost:11434')` — line 970.
- `aws_findings_resource(region: str)` — line 976.
- `aws_cost_summary_resource(region: str)` — line 1002.
- `_ollama_status_endpoint(request: Request)` — line 1018.
- `_ollama_pull_endpoint(request: Request)` — line 1027.
- `_upload_document_endpoint(request: Request)` — line 1042.
- `create_app()` — line 1090.

### `backend/ollama_catalog.py`


### `backend/rag/__init__.py`


### `backend/rag/knowledge_base.py`

- Class `SecurityKnowledgeBase` — line 18.
  - `__init__(self)` — line 19.
  - `encoder(self)` — line 31.
  - `chunk_text(self, text: str)` — line 38.
  - `add_document(self, doc_id: str, text: str, metadata: Optional[dict]=None)` — line 50.
  - `search(self, query: str, n_results: int=3)` — line 77.
  - `delete_document(self, doc_id: str)` — line 98.
  - `list_documents(self)` — line 107.
  - `get_document_count(self)` — line 122.

### `backend/rag/rag_service.py`

- `build_augmented_prompt(query: str, chunks: list[dict])` — line 10.
- `query_knowledge_base(query: str, n_results: int=3, resource_filter: Optional[str]=None)` — line 44.
- `handle_rag_tool_call(tool_input: dict)` — line 113.

### `backend/rag/seed_knowledge.py`

- `seed_all()` — line 242.

### `backend/services/__init__.py`


### `backend/services/aws_scanner.py`

- `get_aws_session(credentials: dict)` — line 9.
- `scan_ec2(region: str='us-east-1', credentials: dict=None)` — line 20.
- `scan_s3(credentials: dict=None)` — line 100.
- `_fetch_user_details(iam_client, user: dict)` — line 194.
- `scan_iam(credentials: dict=None)` — line 265.
- `scan_security_groups(region: str='us-east-1', credentials: dict=None)` — line 311.
- `scan_vpc(region: str='us-east-1', credentials: dict=None)` — line 397.
- `scan_existing_infra_for_context(region: str='us-east-1', credentials: dict=None)` — line 469.
- `scan_sg_usage(region: str='us-east-1', credentials: dict=None)` — line 579.
- `revoke_sg_ingress_rule(sg_id: str, port: int, region: str='us-east-1', credentials: dict=None)` — line 647.

### `backend/services/cost_analyzer.py`

- `get_aws_session(credentials: dict)` — line 14.
- `get_current_month_cost(credentials: dict=None)` — line 25.
- `get_monthly_trend(months: int=3, credentials: dict=None)` — line 89.
- `get_cost_by_service(credentials: dict=None)` — line 154.
- `detect_cost_anomaly(monthly_trend: list)` — line 227.
- `get_cost_insights_llm(current_month: dict, monthly_trend: list, by_service: list, anomaly: dict, model: str='groq', api_key: str=None)` — line 276.

### `backend/services/execution_service.py`

- `_build_env(aws_creds: dict | None)` — line 30.
- `create_execution_id()` — line 45.
- `get_working_dir(execution_id: str)` — line 52.
- `run_terraform_plan(hcl_config: str, execution_id: str, aws_creds: dict | None=None)` — line 59.
- `run_terraform_apply(execution_id: str, aws_creds: dict | None=None)` — line 157.
- `run_terraform_destroy(execution_id: str, aws_creds: dict | None=None)` — line 221.
- `get_key_file(execution_id: str, filename: str)` — line 273.
- `_read_log_unlocked()` — line 293.
- `log_execution(entry: dict)` — line 303.
- `log_execution_update(execution_id: str, updates: dict)` — line 314.
- `get_execution_history()` — line 328.
- `_write_s3_backend_config(workdir: Path, execution_id: str)` — line 338.
- `_cleanup_workdir_plugins(workdir: Path)` — line 361.
- `cleanup_workdir_plugins(execution_id: str)` — line 371.
- `purge_old_workdirs(older_than_days: int=7)` — line 378.
- `_parse_count(output: str, action: str)` — line 399.
- `_parse_applied(output: str)` — line 410.

### `backend/services/llm_service.py`

- `chat_with_groq(message: str, scan_data: dict, history: list=None, api_key: str=None)` — line 12.
- `chat_with_ollama(message: str, scan_data: dict, history: list=None, api_key: str=None, model_name: str=DEFAULT_MODEL)` — line 89.
- `prompt_llm(prompt: str, model: str='groq', api_key: str='', model_name: str=DEFAULT_MODEL)` — line 250.
- `chat_with_anthropic(message: str, scan_data: dict, history: list=None, api_key: str=None)` — line 332.

### `backend/services/ollama_service.py`

- `probe_ollama(base_url: str='http://localhost:11434')` — line 16.
- `stream_ollama_pull(base_url: str, model: str)` — line 85.

### `backend/services/security_analyzer.py`

- `make_finding(finding_id: str, resource_id: str, resource_type: str, rule: str, severity: str, title: str, description: str, recommendation: str, metadata: dict)` — line 6.
- `check_ssh_port_open(security_groups_data: dict)` — line 31.
- `check_rdp_port_open(security_groups_data: dict)` — line 77.
- `check_s3_bucket_public(s3_data: dict)` — line 123.
- `check_iam_user_no_mfa(iam_data: dict)` — line 167.
- `check_unrestricted_traffic(security_groups_data: dict)` — line 210.
- `check_iam_user_inactive(iam_data: dict)` — line 260.
- `check_default_vpc(vpc_data: dict)` — line 332.
- `run_security_analysis(scan_data: dict)` — line 377.

### `backend/services/terraform_service.py`

- `validate_terraform_syntax(hcl: str)` — line 244.
- `generate_terraform_with_anthropic(request: str, api_key: str, existing_infra: str='', validation_error: str='')` — line 288.
- `generate_terraform_with_groq(request: str, api_key: str, existing_infra: str='', validation_error: str='')` — line 320.
- `generate_terraform_with_ollama(request: str, api_key: str, existing_infra: str='', validation_error: str='')` — line 373.
- `generate_terraform(request: str, model: str, api_key: str, existing_infra: str='')` — line 424.
- `handle_summarise_plan(plan_output: str, issue_being_fixed: str, risk_level: str='medium')` — line 460.

### `backend/tests/test_ollama_status.py`

- `_mock_response(status: int, body: dict)` — line 27.
- `_run(coro)` — line 34.
- `test_probe_ollama_ready()` — line 38.
- `test_probe_ollama_unavailable()` — line 71.
- `test_probe_ollama_no_catalog_models()` — line 91.
- `test_stream_pull_auth_error()` — line 114.
- `test_chat_with_ollama_default_model_is_set()` — line 150.

### `backend/tests/test_rag_functional.py`

- `kb()` — line 28.
- Class `TestChunkText` — line 66.
  - `test_short_document_produces_one_chunk(self)` — line 68.
  - `test_long_document_produces_multiple_chunks(self)` — line 78.
  - `test_empty_string_produces_no_chunks(self)` — line 84.
  - `test_whitespace_only_produces_no_chunks(self)` — line 89.
  - `test_chunk_overlap_is_present(self)` — line 94.
  - `test_chunk_size_does_not_exceed_limit(self)` — line 117.
- Class `TestAddDocumentAndSearch` — line 128.
  - `test_add_document_returns_correct_chunk_count(self, kb)` — line 130.
  - `test_add_document_upsert_is_idempotent(self, kb)` — line 137.
  - `test_search_returns_relevant_result_for_known_topic(self, kb)` — line 146.
  - `test_search_top_result_is_correct_document(self, kb)` — line 155.
  - `test_search_returns_relevance_score_field(self, kb)` — line 164.
  - `test_search_scores_are_bounded(self, kb)` — line 173.
  - `test_search_irrelevant_query_scores_low(self, kb)` — line 180.
  - `test_add_then_search_finds_new_document(self, kb)` — line 189.
- Class `TestDeleteAndList` — line 199.
  - `test_list_documents_contains_all_seeded_docs(self, kb)` — line 201.
  - `test_list_documents_structure(self, kb)` — line 215.
  - `test_delete_removes_chunks(self, kb)` — line 225.
  - `test_delete_nonexistent_returns_zero(self, kb)` — line 236.
  - `test_deleted_document_not_in_list(self, kb)` — line 241.
  - `test_deleted_document_not_in_search(self, kb)` — line 249.
- Class `TestBuildAugmentedPrompt` — line 258.
  - `test_prompt_with_chunks_contains_source_labels(self)` — line 275.
  - `test_prompt_with_chunks_contains_relevance_scores(self)` — line 281.
  - `test_prompt_with_chunks_contains_doc_id(self)` — line 287.
  - `test_prompt_with_chunks_contains_chunk_text(self)` — line 292.
  - `test_prompt_with_chunks_contains_the_question(self)` — line 298.
  - `test_context_appears_before_question(self)` — line 304.
  - `test_prompt_with_no_chunks_contains_fallback_message(self)` — line 314.
  - `test_prompt_with_no_chunks_still_contains_question(self)` — line 319.
  - `test_prompt_with_no_chunks_does_not_contain_source_label(self)` — line 325.
- Class `TestQueryKnowledgeBase` — line 331.
  - `test_returns_all_expected_keys(self)` — line 333.
  - `test_chunks_used_lte_chunks_found(self)` — line 339.
  - `test_resource_filter_restricts_results(self)` — line 344.
  - `test_resource_filter_wrong_type_returns_no_chunks(self)` — line 352.
  - `test_sources_list_contains_doc_ids(self)` — line 360.
  - `test_augmented_prompt_is_a_non_empty_string(self)` — line 369.
- Class `TestHandleRagToolCall` — line 376.
  - `test_returns_all_required_keys(self)` — line 378.
  - `test_knowledge_retrieved_is_bool(self)` — line 384.
  - `test_context_is_non_empty_string(self)` — line 389.
  - `test_resource_type_filter_is_passed_through(self)` — line 395.
  - `test_n_results_defaults_to_3(self)` — line 405.
  - `test_n_results_override_respected(self)` — line 410.

### `backend/tests/test_rag_integration.py`

- `get(path, **kwargs)` — line 22.
- `post(path, **kwargs)` — line 25.
- `delete(path, **kwargs)` — line 28.
- `cleanup_test_docs()` — line 33.
- Class `TestListDocuments` — line 42.
  - `test_returns_200(self)` — line 44.
  - `test_response_has_required_fields(self)` — line 48.
  - `test_five_seeded_documents_present(self)` — line 54.
  - `test_seeded_doc_ids_are_present(self)` — line 60.
  - `test_each_document_has_chunk_count_gte_1(self)` — line 70.
  - `test_total_chunks_equals_sum_of_chunk_counts(self)` — line 77.
- Class `TestTextIngest` — line 85.
  - `test_ingest_returns_200(self)` — line 87.
  - `test_ingest_returns_success_status(self)` — line 98.
  - `test_ingest_returns_correct_doc_id(self)` — line 107.
  - `test_ingest_reports_chunks_added(self)` — line 116.
  - `test_ingested_doc_appears_in_list(self)` — line 126.
  - `test_upsert_does_not_duplicate_on_second_ingest(self)` — line 137.
- Class `TestFileUpload` — line 160.
  - `test_txt_upload_returns_200(self, tmp_path)` — line 162.
  - `test_txt_upload_returns_success_status(self, tmp_path)` — line 182.
  - `test_txt_upload_doc_appears_in_list(self, tmp_path)` — line 193.
  - `test_empty_txt_upload_returns_400(self, tmp_path)` — line 205.
- Class `TestDeleteDocument` — line 219.
  - `ensure_test_doc_exists(self)` — line 222.
  - `test_delete_returns_200(self)` — line 231.
  - `test_delete_returns_deleted_status(self)` — line 235.
  - `test_delete_returns_correct_doc_id(self)` — line 239.
  - `test_delete_returns_deleted_chunks_count(self)` — line 243.
  - `test_delete_removes_doc_from_list(self)` — line 248.
  - `test_delete_reduces_total_chunk_count(self)` — line 253.
  - `test_delete_nonexistent_doc_returns_200_with_zero_chunks(self)` — line 259.
  - `test_delete_seeded_doc_then_restore(self)` — line 265.
- Class `TestQueryEndpointRetrieval` — line 282.
  - `test_query_returns_200(self)` — line 289.
  - `test_query_response_has_required_fields(self)` — line 296.
  - `test_query_echoes_question(self)` — line 304.
  - `test_query_returns_non_empty_answer(self)` — line 312.
  - `test_query_retrieves_chunks_for_relevant_question(self)` — line 320.
  - `test_query_sources_are_seeded_doc_ids(self)` — line 329.
  - `test_query_resource_type_filter_restricts_chunks(self)` — line 338.
  - `test_query_raw_chunks_have_relevance_scores(self)` — line 349.
  - `test_query_with_groq_returns_answer(self)` — line 360.
  - `test_query_with_anthropic_returns_answer(self)` — line 369.
  - `test_query_answer_references_source_content(self)` — line 388.

### `backend/tests/test_security_analyzer.py`

- `_sg_data(*entries)` — line 29.
- `_iam_data(*users)` — line 38.
- `_s3_data(*buckets)` — line 42.
- `_vpc_data(*vpcs)` — line 46.
- `test_make_finding_contains_all_nine_fields()` — line 50.
- `test_ssh_open_detects_port_22()` — line 62.
- `test_ssh_open_ignores_other_ports()` — line 71.
- `test_ssh_open_no_internet_exposure()` — line 75.
- `test_ssh_open_status_not_ok()` — line 82.
- `test_ssh_open_empty_dict()` — line 86.
- `test_rdp_open_detects_port_3389()` — line 90.
- `test_rdp_open_ignores_other_ports()` — line 97.
- `test_rdp_open_status_not_ok()` — line 101.
- `test_s3_public_detects_public_bucket()` — line 105.
- `test_s3_public_ignores_private_bucket()` — line 113.
- `test_s3_public_status_not_ok()` — line 117.
- `test_iam_no_mfa_detects_user_without_mfa()` — line 121.
- `test_iam_no_mfa_ignores_user_with_mfa()` — line 129.
- `test_iam_no_mfa_status_not_ok()` — line 133.
- `test_unrestricted_traffic_detects_all_protocol()` — line 137.
- `test_unrestricted_traffic_ignores_specific_protocol()` — line 144.
- `test_unrestricted_traffic_status_not_ok()` — line 148.
- `test_iam_inactive_never_logged_in()` — line 152.
- `test_iam_inactive_login_over_90_days_ago()` — line 160.
- `test_iam_inactive_recent_login_no_finding()` — line 166.
- `test_iam_inactive_status_not_ok()` — line 171.
- `test_default_vpc_detected()` — line 175.
- `test_default_vpc_ignores_custom_vpc()` — line 183.
- `test_default_vpc_status_not_ok()` — line 187.
- `test_run_analysis_findings_sorted_high_to_low()` — line 191.
- `test_run_analysis_empty_scan_data_returns_empty()` — line 206.
- `test_run_analysis_clean_infrastructure_no_findings()` — line 210.

## Every tracked file

85 tracked files: 69 UTF-8 text files and 16 image/binary assets. Dependency lock is included in ZIP but omitted from concatenated source context to reduce noise.

- `.dockerignore` (434 bytes)
- `.gitignore` (601 bytes)
- `aca-diagrams/ACTIVITY_DIAGRAM_AGENT_LOOP.png` (1,236,597 bytes)
- `aca-diagrams/ACTIVITY_DIAGRAM_TERRAFORM_WORKFLOW.png` (1,174,806 bytes)
- `aca-diagrams/COMPONENT_DIAGRAM.png` (987,266 bytes)
- `aca-diagrams/DFD_L0_CONTEXT_DIAGRAM.png` (845,596 bytes)
- `aca-diagrams/DFD_L1_PROCESS_DIAGRAM.png` (2,008,507 bytes)
- `aca-diagrams/ERD_DATA_ENTITY_DIAGRAM.png` (1,633,792 bytes)
- `aca-diagrams/HIGH_LEVEL_REQUEST_FLOW.png` (2,157,984 bytes)
- `aca-diagrams/HIGH_LEVEL_SYSTEM_ARCHITECTURE.png` (95,710 bytes)
- `aca-diagrams/MCP_PROTOCOL_FLOWCHART.png` (1,640,512 bytes)
- `aca-diagrams/SECURITY_ANALYSIS_FLOWCHART.png` (1,980,302 bytes)
- `aca-diagrams/SEQUENCE_DIAGRAM_RAG_QUERY.png` (1,210,341 bytes)
- `aca-diagrams/SEQUENCE_DIAGRAM_SCAN_TO_FIX.png` (288,943 bytes)
- `aca-diagrams/SEQUENCE_DIAGRAM_TERRAFORM_LIFECYCLE.png` (2,728,005 bytes)
- `aca-diagrams/STATE_DIAGRAM_EXECUTION_LOG.png` (1,401,112 bytes)
- `aca-diagrams/SYSTEM_ARCHITECTURE.png` (2,443,963 bytes)
- `backend/.dockerignore` (444 bytes)
- `backend/.env.example` (4,069 bytes)
- `backend/Dockerfile` (1,647 bytes)
- `backend/benchmark.py` (6,935 bytes)
- `backend/docs_generator.py` (8,102 bytes)
- `backend/main.py` (4,175 bytes)
- `backend/mcp_server.py` (40,007 bytes)
- `backend/ollama_catalog.py` (928 bytes)
- `backend/providers_init.tf` (301 bytes)
- `backend/rag/__init__.py` (0 bytes)
- `backend/rag/knowledge_base.py` (4,408 bytes)
- `backend/rag/rag_service.py` (4,062 bytes)
- `backend/rag/seed_knowledge.py` (10,472 bytes)
- `backend/requirements.txt` (191 bytes)
- `backend/scipts/terraform_s3_backend_setup.sh` (10,937 bytes)
- `backend/services/__init__.py` (194 bytes)
- `backend/services/aws_scanner.py` (26,139 bytes)
- `backend/services/cost_analyzer.py` (11,512 bytes)
- `backend/services/execution_service.py` (15,723 bytes)
- `backend/services/llm_service.py` (16,022 bytes)
- `backend/services/ollama_service.py` (4,117 bytes)
- `backend/services/security_analyzer.py` (14,361 bytes)
- `backend/services/terraform_service.py` (22,565 bytes)
- `backend/tests/test_ollama_status.py` (5,661 bytes)
- `backend/tests/test_rag_functional.py` (18,570 bytes)
- `backend/tests/test_rag_integration.py` (16,396 bytes)
- `backend/tests/test_security_analyzer.py` (6,870 bytes)
- `claude_desktop_config_example.json` (1,411 bytes)
- `docker-compose.yml` (1,178 bytes)
- `frontend/.gitignore` (247 bytes)
- `frontend/Dockerfile` (471 bytes)
- `frontend/eslint.config.js` (758 bytes)
- `frontend/index.html` (375 bytes)
- `frontend/nginx.conf` (2,208 bytes)
- `frontend/package-lock.json` (109,250 bytes)
- `frontend/package.json` (698 bytes)
- `frontend/public/favicon.svg` (9,522 bytes)
- `frontend/public/icons.svg` (5,031 bytes)
- `frontend/src/App.css` (74 bytes)
- `frontend/src/App.jsx` (3,163 bytes)
- `frontend/src/api/mcpClient.js` (4,091 bytes)
- `frontend/src/assets/hero.png` (44,919 bytes)
- `frontend/src/components/Dashboard.jsx` (5,884 bytes)
- `frontend/src/components/LoginPage.jsx` (2,761 bytes)
- `frontend/src/components/SettingsModal.jsx` (5,622 bytes)
- `frontend/src/components/panels/ChatPanel.jsx` (3,238 bytes)
- `frontend/src/components/panels/CostPanel.jsx` (4,872 bytes)
- `frontend/src/components/panels/ExecutionHistoryPanel.jsx` (6,149 bytes)
- `frontend/src/components/panels/InfrastructurePanel.jsx` (4,151 bytes)
- `frontend/src/components/panels/KnowledgeBasePanel.jsx` (11,220 bytes)
- `frontend/src/components/panels/SecurityAgentPanel.jsx` (14,334 bytes)
- `frontend/src/components/panels/SecurityPanel.jsx` (8,366 bytes)
- `frontend/src/components/panels/TerraformPanel.jsx` (16,040 bytes)
- `frontend/src/components/tweaks/TweaksPanel.jsx` (7,023 bytes)
- `frontend/src/components/ui/DarkTooltip.jsx` (722 bytes)
- `frontend/src/components/ui/ErrorBoundary.jsx` (990 bytes)
- `frontend/src/components/ui/Field.jsx` (590 bytes)
- `frontend/src/components/ui/Logo.jsx` (418 bytes)
- `frontend/src/components/ui/Topbar.jsx` (1,647 bytes)
- `frontend/src/contexts/ApiKeyContext.jsx` (1,147 bytes)
- `frontend/src/contexts/AuthContext.jsx` (844 bytes)
- `frontend/src/index.css` (2,259 bytes)
- `frontend/src/main.jsx` (271 bytes)
- `frontend/src/styles/globals.css` (23,025 bytes)
- `frontend/src/styles/typography.css` (369 bytes)
- `frontend/src/utils/constants.js` (1,150 bytes)
- `frontend/src/utils/scoring.js` (267 bytes)
- `frontend/vite.config.js` (440 bytes)

## Environment-variable names referenced by Python source

`ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_DEFAULT_REGION`, `GROQ_API_KEY`, `MCP_TRANSPORT`, `OLLAMA_BASE_URL`, `TF_STATE_BUCKET`, `TF_STATE_REGION`

## Validation boundary

- 31 statically registered MCP tools; 2 MCP resources.
- 109 statically named Python test functions/methods across tracked test files; this is a count, not a pass result.
- 28 pure security-analyzer test functions passed by direct invocation in the preceding review.
- Full pytest, frontend build/lint, Docker startup, AWS writes, model calls and database integration were not verified.
