.PHONY: run run_cloud_agent run_edge_vla run_frontend setup clean

run: run_cloud_agent

run_cloud_agent:
	./scripts/run_cloud_agent.sh

run_edge_vla:
	./scripts/run_edge_vla.sh

run_frontend:
	./scripts/run_frontend.sh

setup:
	./scripts/setup.sh

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
