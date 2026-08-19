{{/*
Shared naming and pod-spec fragments.
*/}}

{{- define "frikkinwave.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{/*
Collapse release+chart when the release is already named after the chart, so a
`helm install frikkinwave` yields "frikkinwave-web" rather than the doubled-up
"frikkinwave-frikkinwave-web".
*/}}
{{- define "frikkinwave.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "frikkinwave.labels" -}}
app.kubernetes.io/name: {{ include "frikkinwave.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
{{- end -}}

{{- define "frikkinwave.image" -}}
{{- required "image.repository is required (set by app-deploy.sh)" .Values.image.repository -}}:{{- .Values.image.tag -}}
{{- end -}}

{{/*
The environment every container shares.

envFrom pulls the whole ConfigMap and the whole Terraform-owned Secret, so
adding a config key needs no template change. POD_IP is the exception: it is
per-pod, so it comes from the downward API. production.py appends it to
ALLOWED_HOSTS — without it the kubelet probes and the ALB health check (which
target the pod IP directly) arrive with a Host header Django rejects, every
readiness probe 400s, and no pod ever becomes Ready.
*/}}
{{- define "frikkinwave.envFrom" -}}
- configMapRef:
    name: {{ include "frikkinwave.fullname" . }}-config
- secretRef:
    name: {{ .Values.existingSecret }}
{{- end -}}

{{- define "frikkinwave.podIpEnv" -}}
- name: POD_IP
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
{{- end -}}
