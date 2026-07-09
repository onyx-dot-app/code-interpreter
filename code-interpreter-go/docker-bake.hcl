# Build definitions for the code-interpreter-go images.
#
#   docker buildx bake                    # build both variants
#   docker buildx bake slim               # build one variant
#   VERSION=1.2.3 docker buildx bake --push
#
# Local single-platform build (the docker driver cannot build multi-platform):
#   docker buildx bake --set "*.platforms=linux/amd64" --load
#
# Without a DHI subscription, override the hardened base images:
#   BUILD_IMAGE=golang:1.26-bookworm RUNTIME_IMAGE=debian:bookworm-slim \
#     docker buildx bake

variable "REGISTRY" {
  default = "onyxdotapp"
}

variable "IMAGE_NAME" {
  default = "code-interpreter-go"
}

variable "VERSION" {
  default = "dev"
}

# Base image overrides. Empty means "use the Dockerfile defaults" (the DHI
# hardened images), which stay the single source of truth.
variable "BUILD_IMAGE" {
  default = ""
}

variable "RUNTIME_IMAGE" {
  default = ""
}

group "default" {
  targets = ["full", "slim"]
}

target "_common" {
  context    = "."
  dockerfile = "Dockerfile"
  platforms  = ["linux/amd64", "linux/arm64"]
  args = {
    BUILD_IMAGE   = BUILD_IMAGE != "" ? BUILD_IMAGE : null
    RUNTIME_IMAGE = RUNTIME_IMAGE != "" ? RUNTIME_IMAGE : null
  }
}

# Default image: includes the Docker daemon so every deployment mode works
# out of the box, including Docker-in-Docker.
target "full" {
  inherits = ["_common"]
  tags = [
    "${REGISTRY}/${IMAGE_NAME}:${VERSION}",
    "${REGISTRY}/${IMAGE_NAME}:latest",
  ]
}

# Slim image: no docker packages at all. Opt-in for Docker-out-of-Docker
# (mounted socket) and Kubernetes deployments.
target "slim" {
  inherits = ["_common"]
  args = {
    SKIP_NESTED_DOCKER = "1"
  }
  tags = [
    "${REGISTRY}/${IMAGE_NAME}:${VERSION}-slim",
    "${REGISTRY}/${IMAGE_NAME}:latest-slim",
  ]
}
