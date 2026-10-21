package main

const resourceProposalRecordedMessage = "Proposal recorded only; no host action has occurred. Ask the operator to confirm the exact proposal in Flightdoo. Do not report it as completed."

func resourceMCPToolDefinitions() []map[string]any {
	return []map[string]any{
		{
			"name": "list_resources",
			"description": "Read fresh observations of harness resources and recent action results from this runner. " +
				"Process names, ages, orphan markers, socket states and cached UI context do not prove that anything is unused. " +
				"Missing evidence remains unknown. This tool does not change processes or connections.",
			"inputSchema": map[string]any{
				"type": "object", "properties": map[string]any{}, "additionalProperties": false,
			},
			"annotations": map[string]any{"readOnlyHint": true, "destructiveHint": false, "openWorldHint": false},
		},
		{
			"name": "inspect_resource",
			"description": "Verify one exact resource key against current host observations. " +
				"Check its generation, owner and harness ancestry, protection, state and any incomplete evidence before proposing a change. " +
				"An absent or changed target is not the same resource. No host state is changed.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"resource_key": map[string]any{"type": "string", "minLength": 1, "maxLength": 1024},
				},
				"required": []string{"resource_key"}, "additionalProperties": false,
			},
			"annotations": map[string]any{"readOnlyHint": true, "destructiveHint": false, "openWorldHint": false},
		},
		{
			"name": "propose_action",
			"description": "Record a proposal for an action the operator requested on one currently verified resource. " +
				"Give the evidence, uncertainty and expected interruption in reason. " +
				"Only stop_process, disconnect_mcp and reconnect_mcp are supported. " +
				"This does not stop or disconnect anything. Flightdoo requires the operator to confirm the exact proposal " +
				"before fd-runner revalidates and attempts it; never report a proposal as a completed action.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"resource_key": map[string]any{"type": "string", "minLength": 1, "maxLength": 1024},
					"action_kind": map[string]any{
						"type": "string", "enum": []string{"stop_process", "disconnect_mcp", "reconnect_mcp"},
					},
					"reason": map[string]any{"type": "string", "minLength": 1, "maxLength": 1000},
				},
				"required": []string{"resource_key", "action_kind", "reason"}, "additionalProperties": false,
			},
			"annotations": map[string]any{
				"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false,
			},
		},
	}
}
