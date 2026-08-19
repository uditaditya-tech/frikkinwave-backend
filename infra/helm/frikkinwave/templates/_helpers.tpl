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

{{/*
Kafka client wiring for EVENT_TRANSPORT=kafka.

Only the components that RUN THE RELAY get these — the general worker and the
relay CronJob. Web pods only write the outbox row and nudge Celery, so giving
them broker credentials would widen the blast radius for no functionality.

The Secrets are mirrored into this namespace by Terraform: Kubernetes Secrets
are namespaced and Strimzi creates these in the `kafka` namespace.
*/}}
{{- define "frikkinwave.kafkaEnv" -}}
{{- if .Values.kafka.enabled }}
- name: KAFKA_SASL_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.kafka.userSecret }}
      key: password
{{- end }}
{{- end -}}

{{- define "frikkinwave.kafkaVolumeMounts" -}}
{{- if .Values.kafka.enabled }}
- name: kafka-ca
  mountPath: {{ .Values.kafka.caMountPath }}
  readOnly: true
{{- end }}
{{- end -}}

{{- define "frikkinwave.kafkaVolumes" -}}
{{- if .Values.kafka.enabled }}
- name: kafka-ca
  secret:
    secretName: {{ .Values.kafka.caSecret }}
{{- end }}
{{- end -}}

{{/*
Checksum of the rendered ConfigMap, stamped on every long-lived pod template.

Without this, changing `config` and running `helm upgrade` reports success and
changes NOTHING: envFrom values are injected when a container starts, so a
running pod keeps the old environment forever, and an unchanged pod template
produces no new ReplicaSet to restart it. That is how EVENT_TRANSPORT=kafka
appeared to deploy while every worker stayed on Celery.

CronJobs do not need it — each run creates a fresh pod that reads the current
ConfigMap.
*/}}
{{- define "frikkinwave.configChecksum" -}}
{{- include (print $.Template.BasePath "/configmap.yaml") . | sha256sum -}}
{{- end -}}
