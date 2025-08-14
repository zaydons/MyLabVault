#!/bin/bash

# MyLabVault Development Startup Script

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
print_status() {
	echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
	echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
	echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
	echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Docker is running
check_docker() {
	print_status "Checking Docker status..."
	if ! docker info >/dev/null 2>&1; then
		print_error "🛑 Docker is not running. Please start Docker Desktop and try again."
		exit 1
	fi
	print_success "✅ Docker is running"
}

# Check if docker-compose.yml exists
check_compose_file() {
	if [ ! -f "docker-compose.yml" ]; then
		print_error "🛑 docker-compose.yml not found in current directory"
		print_error "🛑 Please run this script from the project root directory"
		exit 1
	fi
	print_success "✅ Found docker-compose.yml"
}

# Clean up existing containers if needed
cleanup_containers() {
	print_status "Cleaning up existing containers..."
	docker-compose down --remove-orphans >/dev/null 2>&1 || true
	print_success "✅ Cleanup completed"
}

# Start the service
start_services() {
	print_status "Starting MyLabVault server-side rendered application..."
	print_status "Building and starting mylabvault service..."
	
	# Start mylabvault service in detached mode
	docker-compose up --build -d mylabvault
	
	if [ $? -eq 0 ]; then
		print_success "✅ Application started successfully!"
	else
		print_error "🛑 Failed to start application"
		exit 1
	fi
}

# Wait for service to be healthy
wait_for_service() {
	print_status "Waiting for application to be ready..."
	
	# Wait for mylabvault health check
	print_status "Checking application health..."
	timeout=60
	counter=0
	while [ $counter -lt $timeout ]; do
		if docker-compose ps mylabvault | grep -q "healthy"; then
			print_success "✅ Application is healthy and ready!"
			break
		fi
		sleep 2
		counter=$((counter + 2))
		echo -n "."
	done
	
	if [ $counter -ge $timeout ]; then
		print_warning "Health check timed out, but continuing..."
		print_status "You can check the application manually at http://localhost:8000"
	fi
}

# Show service status
show_status() {
	print_status "Application Status:"
	docker-compose ps mylabvault
	
	echo ""
	print_success "✅ MyLabVault is ready!"
	print_status "Access your application at:"
	echo "  - Main Application: http://localhost:8000"
	echo "  - API Documentation: http://localhost:8000/api/docs"
	echo ""
	print_status "Useful commands:"
	echo "  - View logs: docker-compose logs -f mylabvault"
	echo "  - Stop application: docker-compose down"
	echo "  - Restart application: docker-compose restart mylabvault"
}

# Main execution
main() {
	echo "🚀 Starting MyLabVault Application"
	echo "=================================="
	
	check_docker
	check_compose_file
	cleanup_containers
	start_services
	wait_for_service
	show_status
}

# Handle script interruption
trap 'print_error "Script interrupted"; exit 1' INT

# Run main function
main