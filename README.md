# Report Assembly Agent

面向多来源材料的可追溯报告整编系统。材料经解析、理解、证据提取与分析后形成报告；用户可在任务检查点和报告审阅中介入。

## 项目文档

- [当前产品契约](docs/CURRENT_PRODUCT_CONTRACT.md)：任务模式、状态流程、交互、版本与证据边界。
- [当前代码地图](docs/CODEBASE_MAP.md)：主链路和模块入口。
- [模型运行与上下文约定](docs/MODEL_RUNTIME_ARCHITECTURE.md)：阶段预算、并发与硬件复测原则。
- [vLLM 生成服务](docs/VLLM_DEPLOYMENT.md)：部署配置和服务启动。
- [模板与软风格学习架构](docs/template-style-learning.md)：版式、风格画像、范例检索与渲染边界。

## 推理资源边界

管理员在系统设置中配置 vLLM 地址（含 `/v1`）、模型名称和 Key，无需配置或下载 Tokenizer。上下文上限设为 `0` 时通过根路径 `/tokenize` 读取服务实际 `max_model_len`；显式上限不能超过该窗口。最大输出仍由管理员指定，各阶段分别预留输出空间。

装箱只使用本地估算；首次、带工具或接近容量边界的最终请求使用 `/tokenize` 复核同一消息与聊天模板。结果短期缓存，通过实际 `usage.prompt_tokens` 校准估算。计量失败冷却30秒后重试，降级写入调用审计和设置页；未探明窗口且未设置上限时明确报错。超容量请求不会静默删证据或缩减输出，应由阶段重新装箱或拆批。

有效配置以部署环境为准；代码默认值位于 app/config.py。各工作负载的输出预留、输入上限和批次策略由 app/runtime_profiles.py 提供，不能用某一个阶段的 token 比例代表全流程。

```text
阶段可用输入 = 物理上下文窗口 - 阶段输出预留 - Prompt 开销 - 安全余量
```

修改模型、量化、vLLM max-model-len、并发或显卡后，使用相同 benchmark 输入复测 Prefill、Decode、TTFT、端到端耗时、显存和报告质量。具体运行默认值不是远端机器的实际配置。

材料事实必须保持来源链；模型语义判断不得不可逆地删除原始内容。遇到实现与产品契约不一致时，先确认属于代码缺陷还是产品决定变化，再分别修正，不要在多份文档复制同一份流程描述。
# 离线部署授权

Windows 和 Linux 共用离线许可证协议，无需 TPM 或授权服务器。安装完成后须
由管理员登录 `/license` 激活，普通用户仅能查看联系管理员的提示。未授权、
到期、时间回退或授权记录异常时，业务 API 与后台模型执行被阻止；登录、
授权和最小健康检查保持可用。必须启用现有多用户登录并初始化管理员账号。

许可证绑定产品、安装 ID、设备公钥和宿主机标识。同一许可证重复导入不会
重新计时；续期须使用更高代次的新编号，从成功激活起按签发期限计算自然月（UTC，
月末取目标月份最后一天），剩余期限不累加。旧许可证不能回退当前授权。
API 返回 `403 LICENSE_REQUIRED` 时，前端进入授权页。

授权状态位于 `runtime/license`，Windows 安装对应
`%ProgramData%\ReportAssemblyAgent\runtime\license`。正常升级必须保留该目录。
Docker 使用持久 runtime 卷，并只读挂载宿主机 `/etc/machine-id` 为
`/run/ira-host-machine-id`；镜像不携带客户身份或激活记录。Windows 使用
机器范围 DPAPI，仍依靠服务数据目录 ACL 限制访问；Linux 使用权限受限的软件
设备密钥。更换宿主机或丢失设备密钥需要重新申请授权。

## 内部签发流程

内部工具为 `tools/license_issuer.py`，不随应用镜像分发。先在独立受限目录
生成加密签发私钥，密码交互输入；私钥和密码均不得提交 Git 或传给客户。

```bash
python tools/license_issuer.py keygen \
  --key /secure/issuer/issuer.private.pem --output /secure/issuer/public.json
```

将生成的公钥映射更新到 `app/licensing/_trust.pyx` 后重新编译发布版本。
轮换密钥时保留仍在使用的旧公钥；公钥不需要保密。管理员从授权页复制安装
识别码，完整保存为 `request.txt`，签发时严格核对客户和安装归属：

```bash
python tools/license_issuer.py issue \
  --key /secure/issuer/issuer.private.pem \
  --request request.txt --generation 1 --months 12 --customer customer-name \
  --output customer.license
```

将 `customer.license` 交给管理员导入。工具在私钥目录写入 `issuance.jsonl`
台账，拒绝对同一安装签发重复或较低代次。续期改用下一代次和新输出文件；
`--months` 支持 3、6、12、36、60、120，省略时默认 3。现有三个月许可证继续有效。
签发结果显示期限、安装实例和代次，台账记录期限；管理员激活后可查看期限、激活时间和到期时间。
生成日期不会开始授权计时。自动化可使用权限受限的 `--password-file`，
不要在命令行直接传密码。备份私钥、密码和签发台账，建议分开保管。

```bash
python tools/license_issuer.py inspect \
  --request customer.license --trust /secure/issuer/public.json \
  --output inspected-license.json
```

## 原生授权组件构建

运行时不再读取 `trusted_keys.json`。公钥和验签入口编译在 `_trust` 原生模块中；
缺失、损坏或以 Python 文件冒充该模块时拒绝授权，没有 Python 验签降级路径。
开发机器须先用运行服务的同版本 Python 构建：

```bash
python -m pip install -r tools/license-build-requirements.txt
python tools/build_license_native.py --output .native-build
# Linux 源码开发部署仅复制原生信任模块，避免旧编译产物遮蔽后续源码修改
cp .native-build/app/licensing/_trust*.so app/licensing/
```

Docker 多阶段构建自动编译，并在最终镜像移除 protocol/service/storage 的 Python
源文件及 `_trust.pyx`，只保留对应原生模块。最终镜像不包含编译器和签发私钥。
Windows 构建机需要 MSVC C++ Build Tools，`build-native-bundle.ps1` 必须传入
`-LicenseBuildPython`，指向装有构建依赖、与捆绑 Python 版本及架构一致的解释器。
客户离线安装不需要编译器。安装目录仅管理员和 SYSTEM 可写，普通用户仅可读执行；
Docker 程序目录为 root 所有，应用以 ira 用户运行，仅数据目录可写。

原生编译提高替换和逆向成本，不提供硬件信任根。Python 调用层和原生二进制仍可能
被管理员篡改。尚未配置发行者代码签名证书，安装包的 SHA256 只用于传输完整性检查，
不能等同于发行者身份验证；正式发行需另行配置 Windows Authenticode / 包签名。

## 时间与异常恢复

服务每 30 秒持久保存最高观察时间，运行期间结合单调时钟检测回退，容许
300 秒校时偏差但不会因此延长授权。许可证签发时间同时作为激活时间下界。
若服务器曾被误设为未来时间，先校正时间，再由管理员导出恢复请求。内部
审核后签发一次性、状态绑定的时间恢复凭证（七天内有效）：

```bash
python tools/license_issuer.py recover-clock \
  --key /secure/issuer/issuer.private.pem \
  --request license-clock-recovery-request.json --output clock-recovery.license
```

管理员在时间异常页面导入。恢复不改变原激活日期和到期日期，不能用普通
新许可证绕过时间异常。授权文件损坏须恢复一致的身份与状态备份；禁止通过
删除记录尝试重新计时。报告因授权暂停后，由用户续期后点击继续；图谱队列
续期后可继续调度。模板学习暂停保留上传文件，同一服务进程续期后重新学习；
模板任务原有内存状态不跨进程持久化，重启后的模板恢复不在本机制保证范围内。

保护范围：签名防伪造及字段修改；绑定防普通跨安装/跨机器复制；本地校验防
旧证重放、常见改时钟及状态误改。无 TPM、无联网且客户拥有 root/管理员权限
时，完整系统克隆、快照回滚或修改程序可绕过软件保护。本版本未宣称提供
原生代码混淆、可信 UTC、远程吊销或管理员级防破解。迁移到新机器时旧机器
的离线授权不能远程撤销。
