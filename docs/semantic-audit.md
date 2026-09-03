# B134 → kv-transfer-observability 语义核对报告（P0-1）

核对人视角: xiehanlong834-gif（legacy #220 原作者 / 6-event 契约定义者）
日期: 2026-09-02
方法: 对照 `provenance/legacy-patches/core-pr-220/` 0001-0003 实现 patch（+0004 测试契约定义）
      vs 抽取后的 `src/vllm_hust_kv_transfer_observability/{events,descriptors}.py`
      （抽取基线 = main @ 70d1fdc, PR #1）

## 一、保留 ✓

| legacy 语义（#220） | 抽取后 | 判定 |
|---|---|---|
| JSONL sink: O_APPEND\|O_CREAT\|O_WRONLY + 0o600 + threading.Lock 双检 | events.py 同构 | 保留 |
| payload 字段 pid/request_id/ts_monotonic_ns + compact sort_keys | 保留 | 保留 |
| 默认关: 无输出路径时 emit 直接 return | JsonlKVTransferEventSink(None).emit() return | 保留 |
| descriptor: 仅 region-relative 偏移、负值/方向/空清单拒绝 | 保留 + 收紧为严格 schema | 保留 |
| descriptor: O_EXCL 防覆盖 + 0o600 + 短写检测 | 保留 | 保留 |
| evidence_label 四值白名单 | ALLOWED_EVIDENCE_LABELS 保留 | 保留 |
| 文件名 `{pid}-{direction}-job{job_id}.json` | 保留 | 保留 |
| 事件/descriptor 均拒绝进程地址落盘 | 保留 + 测试断言 `"address" not in payload` | 保留（红线） |

## 二、丢失 ✗（需固化/需 host 侧补回）

1. **6-event 恢复链契约整体不在包内**（legacy: preempt / restore_start / restore_done /
   wakeup / admission / scheduled）。抽取后 events.py 是收任意字符串的通用 sink，
   链顺序、触发点、事件名集合都无定义。
   - legacy 由 AST 契约测试固化（test_b134_chain_contract.py）:
     a. scheduler 内 wakeup < admission < scheduled
     b. restore_start < restore_done（tiering manager）
     c. preempt/wakeup/admission/scheduled 不得 gate 在 log_stats 上
     d. cpu_store emit 必须可达（先于 prepare_store 的 return）
   - 这些是"宿主注册后必须满足的顺序/解耦语义"，现在只字未提 → 应写入 HOST_CONTRACT。

2. **transfer_submit / copy_observed_complete 字段契约**:
   - transfer_submit: descriptor_us / dependency_us / submit_us 三段 wall-clock 拆解
   - copy_observed_complete: completion_observed_ms（墙钟观察）与 device_event_ms
     （NPU event）**双时间并存** —— 前提是 Ascend swap_blocks_batch 的 event elapsed 不可靠
     （commit: "Keep completion-observation latency distinct from device event time"）
   - 抽取后 fields 是自由 dict，双时间语义丢失 → 应写入 HOST_CONTRACT。

3. **descriptor region 标识被删**: legacy descriptor 含 `src_region`/`dst_region`
   （如 "src_tensor_0"）；抽取版要求 set(descriptor) == {src_offset, dst_offset, size,
   direction} 精确相等，region 字段被剔除。后果: layout 文件无法回答"这段偏移属于哪个
   tensor region"。HOST_CONTRACT 写 "region-relative offsets and sizes only"，但没定义
   region 边界由谁提供。→ 需设计决策: v1 schema 补 region 字段（如 src_region_id），或
   明确 region 由文件级上下文（direction+job_id 之外的 region 清单文件）提供。

4. **host 侧"禁用时零开销"结构未契约化**: legacy 用 `x = time.monotonic() if EVENTS_ENABLED
   else 0.0` 保证禁用时不读时钟；事件独立性由 AST 测试保证（c 条）。抽取包 sink 层默认关
   零副作用 ✓，但 host adapter 实现时若不继承该结构会引入禁用开销 → HOST_CONTRACT 需声明。

5. **13 个 b134 测试 0 迁移**: 包内现仅 2 测试文件（event 默认关 + descriptor 拒地址 +
   manifest 不可激活）。可迁移的纯语义测试（不依赖 torch/tblib）:
   append 顺序（JSONL 保序）、fail-closed 未知字段拒绝、O_EXCL 冲突、0o600 权限位、
   并发 emit 不丢行、默认关零副作用。→ P0-2 补回。

## 三、有意设计变更（需确认，非丢失）

| 变更 | legacy | 抽取后 | 评估 |
|---|---|---|---|
| 门控机制 | 环境变量 B134_EVENTS_FILE / B134_DESCRIPTOR_LAYOUT_DIR，import 时固定 | 显式构造注入（path 参数 / None=关），由 Extension Manager 配置 | 合理演进（配置外置）；manifest 保留 "filesystem_write" 权限声明 + 显式 operator 目标要求 |
| sink payload schema | 无 schema 字段 | 加 `"schema": "vllm-hust.kv-transfer-event.v1"` | 增强（新包无兼容包袱） |
| event/request_id 校验 | 无 | 非空否则 ValueError | 增强 |
| sink 生命周期 | fd 进程级常驻，无关闭 | close()/context manager | 增强（可测试性） |
| descriptor schema 名 | kv-transfer-descriptor-layout/v1 | vllm-hust.kv-transfer-descriptor-layout.v1 | 命名空间规范化 |

## 四、结论与建议

1. 抽取质量总体高: 安全红线（无地址/0o600/O_EXCL/fail-closed）全部保留且加强。
2. **最大缺口 = 语义没有"契约文本化"**: 6-event 链、双时间、零开销结构都是"原作者脑子里的
   约定"，抽取时没沉淀成文档/测试。Remygred 或其他实现者接 host adapter 时无法对照。
3. 建议动作（P0-4 执行）:
   - HOST_CONTRACT.md 补: 事件名集合与 6-event 链顺序 + 触发层、log_stats 解耦、
     双时间语义、descriptor region 决策、adapter 禁用零开销要求
   - descriptor v1 补 region 字段或声明 region 边界提供方式（需导师/设计拍板）
   - AST 契约测试族应在 host adapter 落地的仓库重建（vllm-hust fork 侧），列入 P1 验收
