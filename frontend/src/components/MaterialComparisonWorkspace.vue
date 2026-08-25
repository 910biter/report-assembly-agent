<script setup lang="ts">
import { computed, ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { api, jsonInit } from "@/api/http";

const props = defineProps<{ reportId: number; versions: any[] }>();
const emit = defineEmits<{ close: []; handoff: [payload: any] }>();
const qc = useQueryClient();
const files = ref<HTMLInputElement>();
const focus = ref("");
const baseVersionId = ref<number | null>(props.versions?.[0]?.id || null);
const selectedId = ref<number | null>(null);
const pending = ref(false);
const error = ref("");
const runs = useQuery({ queryKey: ["material-comparisons", props.reportId], queryFn: () => api<any[]>(`/api/reports/${props.reportId}/material-comparisons`), refetchInterval: 5000 });
const detail = useQuery({ queryKey: ["material-comparison", selectedId], queryFn: () => api<any>(`/api/material-comparisons/${selectedId.value}`), enabled: computed(() => Boolean(selectedId.value)), refetchInterval: q => q.state.data?.status === "ready" || q.state.data?.status === "failed" ? false : 4000 });
const labels: Record<string,string> = { addition:"新增",corroboration:"补强",refinement:"细化",update:"更新",conflict:"冲突",weakening:"削弱",irrelevant:"无关",uncertain:"待确认" };

async function create() {
  if (!files.value?.files?.length) { error.value = "请选择新增材料"; return; }
  pending.value = true; error.value = "";
  try {
    const form = new FormData(); form.set("focus", focus.value);
    if (baseVersionId.value) form.set("base_version_id", String(baseVersionId.value));
    for (const file of files.value.files) form.append("files", file);
    const result = await api<any>(`/api/reports/${props.reportId}/material-comparisons`, { method:"POST", body:form });
    selectedId.value = result.comparison_id; await qc.invalidateQueries({ queryKey:["material-comparisons",props.reportId] });
  } catch (e:any) { error.value=e.message||"创建失败"; }
  finally { pending.value=false; }
}
async function review(item:any,status:string) {
  await api(`/api/material-comparisons/${selectedId.value}/items/${item.id}`,jsonInit("PATCH",{status}));
  await qc.invalidateQueries({queryKey:["material-comparison",selectedId]});
}
async function handoff() {
  try { emit("handoff", await api(`/api/material-comparisons/${selectedId.value}/update-handoff`,{method:"POST"})); }
  catch(e:any){error.value=e.message||"请先接受需要进入报告更新的变化"}
}
</script>

<template>
  <section class="comparison-workspace">
    <header><div><small>独立任务</small><h2>新增材料对比</h2><p>先发现新增、补强、更新和冲突，不直接修改当前报告。</p></div><button class="btn tertiary" @click="$emit('close')">关闭</button></header>
    <div class="comparison-grid">
      <aside>
        <div class="create-box"><label>对比基线<select v-model="baseVersionId"><option v-for="version in versions" :key="version.id" :value="version.id">版本 {{ version.version_label || version.version_no }}</option></select></label><label>新增材料<input ref="files" type="file" multiple></label><label>本次关注<textarea v-model="focus" rows="2" placeholder="可选；不填写则自动判断全部变化"></textarea></label><button class="btn primary" :disabled="pending" @click="create">{{pending?'正在创建…':'开始异步对比'}}</button></div>
        <button v-for="run in runs.data.value||[]" :key="run.id" class="run-item" :class="{active:selectedId===run.id}" @click="selectedId=run.id"><b>{{run.created_at}}</b><span>{{run.status==='ready'?'等待审阅':run.status==='failed'?'失败':'运行中'}} · {{run.summary?.new_fact_count||0}} 条新事实</span></button>
      </aside>
      <main v-if="detail.data.value">
        <div class="summary-strip"><div><strong>{{detail.data.value.summary?.new_fact_count||0}}</strong><span>新增事实</span></div><div><strong>{{detail.data.value.summary?.change_counts?.conflict||0}}</strong><span>冲突</span></div><div><strong>{{detail.data.value.summary?.affected_sections?.length||0}}</strong><span>受影响章节</span></div><div><strong>否</strong><span>已修改报告</span></div></div>
        <div v-if="detail.data.value.status!=='ready'" class="notice">{{detail.data.value.status==='failed'?detail.data.value.error:'系统正在解析和比较新增材料，完成后可集中审阅。'}}</div>
        <article v-for="item in detail.data.value.items||[]" :key="item.id" class="change-item" :class="item.change_type"><div class="change-head"><div><span class="badge">{{labels[item.change_type]||item.change_type}}</span><b>{{item.title}}</b></div><span>{{item.confidence==='high'?'高':item.confidence==='low'?'低':'中'}}置信</span></div><p>{{item.rationale}}</p><div class="evidence-compare"><div><small>基线事实</small><p>{{item.evidence?.baseline_fact?.content||'无对应事实'}}</p></div><div><small>新增事实</small><p>{{item.evidence?.new_fact?.content}}</p></div></div><p class="muted">影响位置：{{item.impact?.report_locations?.map((x:any)=>x.section).filter(Boolean).join('、')||'尚未映射到既有章节'}}</p><div class="button-row"><button class="btn" :class="{primary:item.status==='accepted'}" @click="review(item,'accepted')">纳入更新</button><button class="btn tertiary" @click="review(item,'needs_verification')">待核验</button><button class="btn tertiary" @click="review(item,'ignored')">忽略</button></div></article>
        <button v-if="detail.data.value.status==='ready'" class="btn primary handoff" @click="handoff">将已选变化交给现有增量更新</button>
      </main>
      <div v-else class="empty"><div><strong>选择一次对比</strong>查看新增材料相对于报告基线带来的变化。</div></div>
    </div><p v-if="error" class="error-text">{{error}}</p>
  </section>
</template>

<style scoped>
.comparison-workspace{position:fixed;inset:calc(var(--header-height) + 16px) 20px 20px;z-index:40;display:grid;grid-template-rows:auto 1fr;overflow:hidden;background:#f5f6f8;border:1px solid var(--color-border-strong);box-shadow:var(--shadow-drawer)}.comparison-workspace>header{display:flex;justify-content:space-between;padding:20px 24px;background:#fff;border-bottom:1px solid var(--color-border)}.comparison-workspace h2,.comparison-workspace p{margin:0}.comparison-workspace header small{color:var(--color-primary);font-weight:650}.comparison-grid{display:grid;grid-template-columns:300px 1fr;min-height:0}.comparison-grid>aside,.comparison-grid>main{overflow:auto}.comparison-grid>aside{padding:16px;background:#fff;border-right:1px solid var(--color-border)}.comparison-grid>main{padding:24px 30px}.create-box{display:grid;gap:10px;padding-bottom:18px;border-bottom:1px solid var(--color-border)}.create-box label{display:grid;gap:4px;color:var(--color-muted);font-size:12px}.run-item{display:grid;width:100%;padding:12px 8px;text-align:left;border:0;border-bottom:1px solid var(--color-border);background:transparent}.run-item.active{color:var(--color-primary);background:var(--color-primary-soft)}.run-item span{color:var(--color-muted);font-size:12px}.summary-strip{display:grid;grid-template-columns:repeat(4,1fr);margin-bottom:20px;background:#fff;border:1px solid var(--color-border)}.summary-strip>div{padding:14px 18px;border-right:1px solid var(--color-border)}.summary-strip>div:last-child{border:0}.summary-strip strong,.summary-strip span{display:block}.summary-strip strong{font-size:22px}.summary-strip span{color:var(--color-muted);font-size:12px}.change-item{margin-bottom:12px;padding:16px 18px;background:#fff;border:1px solid var(--color-border);border-left:3px solid #8093a7}.change-item.conflict,.change-item.weakening{border-left-color:#b35b4d}.change-item.addition,.change-item.refinement{border-left-color:#527fa8}.change-head,.change-head>div{display:flex;align-items:center;justify-content:space-between;gap:10px}.change-head>span{color:var(--color-muted);font-size:12px}.evidence-compare{display:grid;grid-template-columns:1fr 1fr;margin:12px 0;border:1px solid var(--color-border)}.evidence-compare>div{padding:12px}.evidence-compare>div+div{border-left:1px solid var(--color-border);background:#fafbfd}.evidence-compare small{color:var(--color-muted)}.handoff{position:sticky;bottom:0;float:right}.notice{padding:18px;background:#fff;border:1px solid var(--color-border)}@media(max-width:900px){.comparison-grid{grid-template-columns:1fr}.comparison-grid>aside{display:none}.comparison-workspace{inset:var(--header-height) 0 0}.summary-strip{grid-template-columns:1fr 1fr}}
</style>
