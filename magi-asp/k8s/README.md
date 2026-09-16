# magi-asp on Kubernetes

ASP is one Deployment. Each MAGI start is its own Pod (`magi <handle> <base> <token>`). The desktop has no deploy scripts.

```bash
kubectl apply -k magi-asp/k8s
```

Build images from the repo root:

```bash
docker build -f magi-asp/Dockerfile -t magi-asp:0.1.0 .
docker build -f py-magi/Dockerfile -t magi:0.1.0 .
```

MAGI pods reach ASP at `http://magi-asp:42069`. ASP creates those pods in-cluster (Role on `pods`).
