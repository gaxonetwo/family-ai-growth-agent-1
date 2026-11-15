# 反馈文本分类规则设计

状态：阶段1最后一次结构修订；阶段2.1按此契约实现本地分类引擎，等待用户验收。

规则版本：`1.0.0`。编写日期：2026-11-15。版本固定数据契约，不表示分类效果已获用户验收。

## 1. 分类目标与业务价值

将反馈文本转成可解释的主题和情感，帮助运营人员识别用户在谈论什么、表达什么态度，为后续主题占比、高频问题、负面反馈趋势和渠道对比提供依据。

主题回答“谈论什么”，情感回答“态度如何”。两个维度分别匹配，不得根据主题直接推断情感。`positive` 只能作为情感值，不能作为主题值。例如设备连接也可以得到正面评价，性能建议也可以是中性。

本阶段只记录规则。后续采用 Python 本地规则，不使用大模型 API；不读取 ground_truth，不向正式数据库写入标签。本文15种文本和频次来自阶段0正式数据库调查，不是离线评估答案。原始文本必须保留，规范化只作用于分类副本。

## 2. 主题枚举与边界

首版每条反馈只输出一个主题。

| 主题值 | 业务定义 | 代表依据 | 边界 |
|---|---|---|---|
| device_connection | 配网、设备连接、掉线、重连及连接稳定性 | 配网、设备连接、设备离线 | 不从“慢”单字推断连接问题，也不推断网络/固件根因 |
| onboarding | 注册后下一步、首次使用、新手引导流程 | 新手引导、第一次使用、注册后不知道下一步 | 普通功能入口问题不自动算新手引导 |
| feature_discovery | 功能入口可见性、功能介绍、功能发现和使用提示 | 功能入口、首页找到功能、功能介绍 | 有明确注册/首次使用流程时按具体重叠规则处理 |
| performance | 页面加载、操作响应、应用运行速度与流畅度 | 页面加载、响应时间、运行流畅度 | 配网步骤“不顺畅”不等于应用性能问题 |
| general_experience | 没有更具体主题的整体体验、流程易用性和总体功能价值评价 | 整体体验、流程清晰、更新实用 | 可以是正面、负面或中性；具体设备/性能等主题优先 |
| unknown | 信息不足或存在未解决的多个独立主题；无效输入也保留此标签 | 无法理解、多主题冲突 | 空白另标invalid_input；必须区分数据质量与正常分类未知 |

`general_experience` 不是“正面主题”。“整体体验很差”应为 general_experience / negative；“设备连接很稳定”应为 device_connection / positive。

## 3. 情感枚举与判断标准

| 情感值 | 判断标准 | 示例 |
|---|---|---|
| positive | 明确表扬、肯定或满意表达 | 体验不错、比较方便、连接稳定 |
| negative | 明确故障、抱怨或体验不足 | 中断、离线、加载慢、不够明显、不知道下一步 |
| neutral | 仅建议、请求或无明确情感倾向的事实陈述 | 希望增加新手引导、希望优化流畅度 |
| unknown | 无法理解、没有可用判断依据，或正负表达冲突 | 无法辨认的文本、同时明确表扬和抱怨 |

neutral 不代表满意，也不代表没有问题。unknown 不得为了统计方便并入 neutral。不能因为出现“希望”就判 positive 或 neutral，必须先检查其余文本是否明确报告问题。

## 4. 全部15种原始文本及预期结果

以下15种原文及数量完整保留阶段0调查结果，合计186条。表中主题、情感及理由为已审核设计预期；阶段2.1使用独立文本测试核对这些预期，不代表重新查询正式数据库或验证真实分类准确率。

| 序号 | 原始文本（不得改写） | 数量 | 预期主题 | 预期情感 | 判断理由 |
|---|---|---:|---|---|---|
| 1 | 希望在首页更容易找到常用功能。 | 22 | feature_discovery | neutral | 首页功能可发现性；只有优化建议，无明确故障或负面评价 |
| 2 | 整体使用体验不错，常用功能比较方便。 | 18 | general_experience | positive | 总体体验评价，“不错”“方便”明确肯定 |
| 3 | 操作流程清晰，使用起来比较顺手。 | 18 | general_experience | positive | 总体流程易用性，“清晰”“顺手”明确肯定，无首次使用限定 |
| 4 | 最近的功能更新比较实用。 | 16 | general_experience | positive | 总体功能价值评价，“实用”明确肯定 |
| 5 | 没有注意到部分功能入口。 | 13 | feature_discovery | neutral | 描述个人未注意到入口，未明确断言入口难找或设计差 |
| 6 | 页面加载有时比较慢。 | 12 | performance | negative | 页面加载慢是明确性能不足，“有时”不改变问题性质 |
| 7 | 第一次使用时不太清楚从哪里开始。 | 12 | onboarding | negative | 首次使用不知从何开始，明确流程引导不足 |
| 8 | 功能介绍不够明显，需要更多使用提示。 | 12 | feature_discovery | negative | “不够明显”是明确体验不足，功能介绍是具体对象 |
| 9 | 完成注册后，不知道下一步应该使用什么功能。 | 11 | onboarding | negative | 注册后的下一步不清楚，明确新手流程问题 |
| 10 | 部分操作响应时间较长。 | 10 | performance | negative | 明确描述响应耗时过长 |
| 11 | 设备有时显示离线，重新连接后恢复。 | 10 | device_connection | negative | 明确掉线问题；恢复是处理结果，不是表扬 |
| 12 | 配网步骤不太顺畅，希望提供更清晰的指引。 | 9 | device_connection | negative | 配网是具体对象；“不太顺畅”已明确负面，后半句建议不抵消问题 |
| 13 | 设备连接偶尔中断，希望连接过程更稳定。 | 9 | device_connection | negative | 明确连接中断；“希望”不覆盖故障描述 |
| 14 | 希望增加更直观的新手引导。 | 7 | onboarding | neutral | 明确建议，无“不直观”等对现状的直接负面断言 |
| 15 | 希望优化应用运行流畅度。 | 7 | performance | neutral | 性能改进请求，没有明确“卡顿”等故障描述 |

预期主题合计：general_experience=52，feature_discovery=47，onboarding=30，performance=29，device_connection=28，unknown=0。

预期情感合计：positive=52，negative=85，neutral=49，unknown=0。这些数值只用于说明本版本人工口径的结果，不得硬编码为分析函数输出。

## 5. 已知歧义与具体处理

| 文本序号/情况 | 歧义 | 具体处理规则 | is_ambiguous |
|---|---|---|---|
| 1、14、15 | 改进建议可能隐含不满 | S.NEUTRAL.SUGGESTION：无明确评价时取neutral，记录隐含不满的可能性 | true |
| 5 | 未注意到入口，可能是设计不足或个人未发现 | S.NEUTRAL.OBSERVATION：此特定客观陈述取neutral；“入口很难找”则为negative | true |
| 3 | 流程评价可能涉及新手引导 | 无首次使用/注册背景时取general_experience，不推断新手场景 | false |
| 8 | 功能介绍与新手引导重叠 | C.OVERLAP.FEATURE_HINT：无新手或注册场景、只有功能介绍和提示时取feature_discovery | true |
| 9 | 注册后提到功能，可能误触功能发现 | C.OVERLAP.POST_SIGNUP：注册后不知道下一步，且无独立入口问题时取onboarding | true |
| 11 | 恢复可能被误判为正面 | “恢复”是故障处理结果，不单独作为肯定证据；保留negative | false |
| 12 | 指引的指代需要结合配网上下文解释 | C.OVERLAP.SETUP_GUIDANCE：指引指向同一设备配置流程，只有device_connection候选；无首次使用/注册语境不生成onboarding候选；不把配网不顺畅当性能 | true |
| 13 | 故障与建议同时出现 | S.NEGATIVE.PROBLEM：故障明确且没有独立肯定时取negative | false |

`is_ambiguous=true` 可以与已确定标签同时存在：它表示存在其他合理解释，本版本通过明确约定完成处理。unknown表示规则无法可靠作决定，二者不是同义词。

已解决的歧义写入 ambiguity_reasons；未解决的冲突写入 conflict_reasons。不能将规则未覆盖的真实多主题简单视为已解决。

## 6. 主题匹配与优先级

名义优先顺序：

`device_connection > onboarding > performance > feature_discovery > general_experience > unknown`

此顺序仅用于已明确批准的重叠规则，不能作为“多个主题一律选排第一”的通用办法。

匹配流程：

1. 保留原文；只在匹配副本上去除首尾空白、统一空白。不得删除“不、没、无法”等否定词。
2. 用对象和上下文生成候选主题；例如“页面”与“加载”共同支持性能，“设备”与“连接/离线”支持连接。单独的“慢、希望、功能”不够。
   “指引、说明、教程”等词本身也不足以生成onboarding候选，必须有新手、首次使用或注册后下一步等对应语境。“配网步骤不太顺畅，希望提供更清晰的指引”中的指引只指向配网，候选只有device_connection。任何额外候选都必须记录独立的主题对象、上下文和原文证据；不能因为它在主题优先级表中存在就加入。
3. 判断命中证据是否指向同一件事。已知同对象重叠按第5节具体规则消解。
4. 剩余唯一主题时输出该主题；多个证据支持同一主题仍只计一个主题。
5. 存在两个或更多独立主题且没有具体消解规则时输出unknown，保存候选主题和冲突理由。
6. 无可靠主题时输出unknown。不能默认归入general_experience。

通用体验词如果只是修饰具体对象，不另算独立主题。例如“设备连接很好用”是连接主题；“设备连接很好，但整体操作流程复杂”包含两个独立评价对象，主题为unknown。

建议规则ID：C.DEVICE.CONNECTION、C.DEVICE.SETUP、C.ONBOARDING.FIRST_USE、C.ONBOARDING.NEXT_STEP、C.PERFORMANCE.LOAD、C.PERFORMANCE.RESPONSE、C.PERFORMANCE.FLUENCY、C.FEATURE.ENTRY、C.FEATURE.DESCRIPTION、C.GENERAL.EXPERIENCE，以及第5节的C.OVERLAP规则。

“设备一直掉线，而且页面加载很慢”必须为unknown；不能因device_connection排序更高而丢弃性能问题。其情感仍可独立确定为negative。

### multiple_independent_topics的判定条件

必须同时满足：

1. 至少有两个不同的非unknown主题候选，各有具体对象及其问题、评价或请求的原文证据；不是只因出现多个关键词。
2. 各证据能够形成独立命题，分别描述不同问题或评价对象；删去其中一个命题后，另一主题的完整表达仍成立。
3. 不是同一对象的修饰、故障及恢复、原因及改进建议，也不是多个证据共同支持同一主题。
4. 第5节已确认的具体重叠规则不能将这些命题解释为同一个主题，无法可靠选出唯一主题。

满足条件时：category=unknown，category_rule=C.UNKNOWN.MULTIPLE_TOPICS，category_unknown_reason=multiple_independent_topics；保留全部category_candidates及对应证据，is_ambiguous=true，conflict_reasons逐项写出对象与证据。情感另行判断，不能跟随主题自动变为unknown。

例如“设备掉线，而且页面卡顿”是两个独立问题；“配网不顺畅，希望提供配网指引”是同一问题与建议；“页面加载慢、操作响应也慢”是同一performance主题。对无法确认是否独立的表达保守返回unknown，使用ambiguous_topic原因，不强行套用主题优先级。

## 7. 情感匹配与优先级

情感不读取最终主题标签，不以“连接=负面、总体体验=正面”推导结果。

按以下决策顺序处理：

1. 无效或空白输入：S.INVALID_INPUT.TYPE / S.INVALID_INPUT.EMPTY，classification_status=invalid_input；非空但无法理解属于S.UNKNOWN.UNRECOGNIZED，不标为数据质量问题。
2. 先处理否定及表达范围，再识别完整短语。“体验不错”是肯定，“不好用”是负面；不能仅扫描“不”字。
3. 同时存在明确肯定与明确抱怨，且未有具体消解规则：S.UNKNOWN.CONFLICT，返回unknown，记录两种证据。
4. 有明确问题，且没有冲突肯定：S.NEGATIVE.PROBLEM，返回negative；问题后接建议仍为negative。
5. 有明确肯定，且没有冲突问题：S.POSITIVE.PRAISE，返回positive；肯定后接不含明确问题的建议仍为positive。
6. 只有改进建议：S.NEUTRAL.SUGGESTION，返回neutral。
7. 已识别的无倾向事实陈述：S.NEUTRAL.OBSERVATION，返回neutral。
8. 其余：S.UNKNOWN.UNRECOGNIZED，返回unknown，不能把“没匹配到情感词”一律算neutral。

冲突判定优先于正/负标签；neutral不能覆盖已有明确肯定或明确问题。规则优先级是判断顺序，不是“负面永远压过正面”。

首版只支持已明确列出的否定短语；未覆盖的复杂否定、反讽、历史问题与当前评价冲突、引用他人评价等应保守标记unknown并等待扩充规则。例如“以前慢，现在很好用”包含时间变化，首版不自动只取最后一句。

## 8. unknown触发条件与两个维度独立输出

主题unknown：空白/无效输入；无可靠对象；未支持领域；独立多主题未解决；语义或指代不清。

情感unknown：空白/无效输入；无法理解；只有规则不认识的表达；明确正负冲突；未覆盖否定、反讽或时间变化导致结论不可靠。

两个维度可以只有一个unknown：

- “设备连接很好，但偶尔断开”：主题device_connection，情感unknown。
- “设备掉线而且页面加载很慢”：主题unknown，情感negative。
- “价格太贵了”：首版无价格主题，主题unknown，情感可由明确抱怨短语确定为negative。
- “今天天气怎么样”：两个维度均unknown，不当作中性产品反馈。

Python None、数字等非字符串输入属于invalid_input，不强制转为字符串；字符串"None"不是Python None，应作为非空文本按普通未知语义处理。具体公共函数类型签名在阶段2确定。

### 输入状态与unknown原因契约

classification_status为记录级状态，优先判断输入有效性：

| 状态 | 条件 | 两维度标签与原因 |
|---|---|---|
| invalid_input | 非字符串、空字符串、去除首尾空白后为空 | 两标签均unknown；两原因均为non_string_input或empty_text |
| classified | 有效非空字符串，主题和情感均非unknown | 两原因均为null；已按具体规则解决歧义也可属于此状态 |
| partially_classified | 有效非空字符串，仅一个维度为unknown | 只有该维度填写原因，另一个为null |
| unknown | 有效非空字符串，两个维度均为unknown | 分别填写各自原因，不标invalid_input |

category_unknown_reason允许值：empty_text、non_string_input、no_supported_topic、ambiguous_topic、multiple_independent_topics。

sentiment_unknown_reason允许值：empty_text、non_string_input、no_supported_sentiment、conflicting_sentiment、unsupported_context。

审核意见中的unknown_reason采用两个独立字段category_unknown_reason和sentiment_unknown_reason表达，不再增加含义重叠的单一unknown_reason字段。非unknown标签的对应原因必须为null。

空白示例：category=unknown、sentiment=unknown、category_rule=C.INVALID_INPUT.EMPTY、sentiment_rule=S.INVALID_INPUT.EMPTY、classification_status=invalid_input，两原因均为empty_text；两个候选列表和命中证据均为空，is_ambiguous=false。它是数据质量问题，不是语义冲突。

E20“今天天气怎么样？”则为classification_status=unknown，两原因为no_supported_topic和no_supported_sentiment。E15为partially_classified；E16为partially_classified；E17为unknown。E21原案例及标签保留，状态补充为invalid_input。

后续汇总须单列invalid_input数量和正常有效文本中的未知数量，不把两者相加后称为“无法理解的反馈”。原始总数仍包括所有记录，并保证可对账。

### 唯一规则ID登记

主题规则ID一律以C开头，情感规则ID一律以S开头，大小写固定；每个ID在登记表只定义一种含义，可被多条反馈复用。category_rule和sentiment_rule分别记录各维度最终决策ID，禁止共用一个不带维度的UNKNOWN.INVALID_INPUT。

| 唯一规则ID | 含义 |
|---|---|
| C.DEVICE.CONNECTION | 设备连接、离线、重连及稳定性对象 |
| C.DEVICE.SETUP | 设备配网对象 |
| C.ONBOARDING.FIRST_USE | 首次使用/新手教程对象 |
| C.ONBOARDING.NEXT_STEP | 新用户下一步流程对象 |
| C.PERFORMANCE.LOAD | 页面加载对象 |
| C.PERFORMANCE.RESPONSE | 操作响应耗时对象 |
| C.PERFORMANCE.FLUENCY | 应用运行流畅度对象 |
| C.FEATURE.ENTRY | 功能入口与发现对象 |
| C.FEATURE.DESCRIPTION | 功能介绍与使用提示对象 |
| C.GENERAL.EXPERIENCE | 无更具体对象的总体体验或功能价值 |
| C.OVERLAP.FEATURE_HINT | 第5节功能介绍/提示的已知重叠 |
| C.OVERLAP.POST_SIGNUP | 第5节注册后下一步的已知重叠 |
| C.OVERLAP.SETUP_GUIDANCE | 第5节同一配网流程及指引的已知重叠 |
| C.INVALID_INPUT.EMPTY | 空白输入的主题回退 |
| C.INVALID_INPUT.TYPE | 非字符串输入的主题回退 |
| C.UNKNOWN.MULTIPLE_TOPICS | 多个独立主题未解决 |
| C.UNKNOWN.AMBIGUOUS | 无法可靠确定唯一主题 |
| C.UNKNOWN.UNSUPPORTED | 无受支持主题证据 |
| S.POSITIVE.PRAISE | 明确肯定、无冲突问题 |
| S.NEGATIVE.PROBLEM | 明确问题、无冲突肯定 |
| S.NEUTRAL.SUGGESTION | 只有改进建议 |
| S.NEUTRAL.OBSERVATION | 已识别的无倾向事实陈述 |
| S.INVALID_INPUT.EMPTY | 空白输入的情感回退 |
| S.INVALID_INPUT.TYPE | 非字符串输入的情感回退 |
| S.UNKNOWN.CONFLICT | 明确正负冲突 |
| S.UNKNOWN.CONTEXT | 复杂否定、反讽等未覆盖语境 |
| S.UNKNOWN.UNRECOGNIZED | 无可靠情感依据 |

### 多规则命中的确定性决策顺序

先完成输入有效性及第6、7节冲突判定，真实独立多主题和正负冲突仍走各自的unknown规则。以下排序只决定已经兼容、不会改变唯一标签的命中规则中，哪一个作为最终决策ID，不能拿来解决真实主题冲突。

1. 已批准且满足全部上下文条件的具体重叠规则优先。规则名称含OVERLAP不意味着必须产生两个主题候选，也可以消解同一设备流程内的指代。
2. 其余兼容命中中，对象明确的具体短语规则优先于一般关键词组合规则。例如完整短语“功能介绍入口”优先于分开出现的“功能”“入口”关键词组合；不能用单个“指引”生成新手主题。
3. 同一层级使用下表显式固定顺序；不按运行时字典/集合遍历顺序、规则登记表行序或文本中先出现的候选作决定。

| 层级 | 兼容命中的固定规则顺序（从左到右） |
|---|---|
| 已批准具体重叠规则 | C.OVERLAP.SETUP_GUIDANCE > C.OVERLAP.POST_SIGNUP > C.OVERLAP.FEATURE_HINT |
| 普通主题规则，先比较具体短语/一般组合层级，再用本行顺序 | C.DEVICE.SETUP > C.DEVICE.CONNECTION > C.ONBOARDING.NEXT_STEP > C.ONBOARDING.FIRST_USE > C.PERFORMANCE.LOAD > C.PERFORMANCE.RESPONSE > C.PERFORMANCE.FLUENCY > C.FEATURE.ENTRY > C.FEATURE.DESCRIPTION > C.GENERAL.EXPERIENCE |
| 情感规则 | 先按第7节判断；无冲突的明确问题用S.NEGATIVE.PROBLEM，明确肯定用S.POSITIVE.PRAISE；只有中性兼容证据时S.NEUTRAL.SUGGESTION > S.NEUTRAL.OBSERVATION |

情感行不是正负标签的覆盖排序，正负明确共存时必须先用S.UNKNOWN.CONFLICT。invalid_input和其他unknown回退规则按各自触发条件选取，不参与上述兼容规则排序。

阶段2的每条匹配定义须标记match_kind为specific_phrase或keyword_combination；“具体短语”必须在规则中显式登记，不以字符串长度临时猜测。相同层级、相同规则ID有多个命中片段时，不再改变最终ID；保留全部片段，按原文出现顺序输出。同一起点时按片段长度降序、再按文本字典序排列，保证输出顺序也稳定。

示例“建议增加一个功能介绍入口。”：C.FEATURE.ENTRY的具体短语“功能介绍入口”与C.FEATURE.DESCRIPTION的具体短语“功能介绍”兼容命中，同层级由固定顺序选C.FEATURE.ENTRY；category_candidates只有feature_discovery一个主题；sentiment_rule独立为S.NEUTRAL.SUGGESTION。

所有命中证据保存在独立的matched_rules中；matched_keywords仅提供按维度去重的证据片段摘要。ambiguity_reasons只记录语义歧义，conflict_reasons只记录未解决的真实冲突，二者均不承担规则匹配日志职责。每条结果始终保存独立的category_rule、sentiment_rule，不因选定最终ID而丢弃其他命中依据。

## 9. 规则匹配结果结构

以下为数据契约示例，不是可执行分类器或运行结果：

```json
{
  "category": "device_connection",
  "sentiment": "negative",
  "category_rule": "C.OVERLAP.SETUP_GUIDANCE",
  "sentiment_rule": "S.NEGATIVE.PROBLEM",
  "matched_keywords": {
    "category": ["配网", "指引"],
    "sentiment": ["不太顺畅"]
  },
  "is_ambiguous": true,
  "ambiguity_reasons": ["指引指向同一设备配置流程；无新手或注册语境，不产生onboarding候选"],
  "conflict_reasons": [],
  "category_candidates": ["device_connection"],
  "sentiment_candidates": ["negative"],
  "matched_rules": [
    {
      "dimension": "category",
      "rule_id": "C.OVERLAP.SETUP_GUIDANCE",
      "match_kind": "keyword_combination",
      "matched_text": "配网步骤不太顺畅，希望提供更清晰的指引",
      "start": 0,
      "end": 19,
      "selected": true
    }
  ],
  "classification_status": "classified",
  "category_unknown_reason": null,
  "sentiment_unknown_reason": null,
  "rule_version": "1.0.0"
}
```

category_rule与sentiment_rule始终存在，unknown也有对应规则ID。matched_keywords必须记录实际命中的原文片段，按维度区分；没有证据时为空列表，不编造关键词。关键词多处重复只保留一次，按首次出现顺序排列。

上述15个字段均为必填结构字段，不能省略为unknown的维度。category_candidates和sentiment_candidates去重，只存已识别的非unknown候选；无匹配为空列表。matched_rules中的每个实际命中记录dimension、rule_id、match_kind、matched_text、start、end和selected，示例中的匹配列表为结构节选，实际返回须包含所有主题和情感命中。conflict_reasons解释冲突，证据对应关系由matched_rules保存。

start/end是原始Python字符串的Unicode字符索引，采用左闭右开区间，始终满足原文[start:end] == matched_text；不是UTF-8字节位置或浏览器UTF-16位置。规范化仅操作副本并维护到原文的位置映射。keyword_combination使用覆盖组合证据的最小连续区间，允许包含连接词；不篡改原文。

matched_rules稳定按主题维度在前、情感在后，再按start升序、区间长度降序、rule_id及match_kind排序。多处命中保留不同位置；相同规则/位置/类型去重。selected表示该命中的rule_id等于该维度最终决策ID，重复出现的同一最终规则可以有多个selected=true。

invalid_input或没有文本命中的unknown回退决策仍记录category_rule和sentiment_rule，但不虚构命中证据，matched_rules为空或只保留实际候选命中。冲突回退时保留候选匹配且selected=false；语境保护规则若有实际命中则可selected=true。不要求每个维度都有selected=true。

无命中时is_ambiguous可以为false；有冲突或第5节已确认的歧义时为true。conflict_reasons保留未解决冲突，ambiguity_reasons说明已解决的解释分歧。保留所有候选标签方便复核，不输出伪造的置信度概率。

## 10. 规则版本管理

- 数据契约版本固定为1.0.0，实际代码输出必须一致；分类引擎测试情况以阶段2.1交付报告为准，版本不代替验收。
- MAJOR：枚举、单/多主题策略或核心情感口径发生不兼容变化。
- MINOR：增加近义表达、具体重叠规则或领域规则，可能改变部分旧结果，必须列出变化样本。
- PATCH：不改变预期分类结果的说明、错误字和规则ID注释修正。任何会改变标签的修改不能仅按文档修正处理。
- 每个新版本记录日期、改动、旧/新预期、原因及测试结果。分析输出必须携带rule_version，不混合不同版本直接比较趋势。
- 规则ID语义稳定；规则废弃后保留迁移说明，不复用旧ID表达不同含义。
- 正式文本及额外案例作为后续回归样本；扩充时加入冲突与unknown案例，不能只追求分类覆盖率。

### 变更记录

| 版本 | 日期 | 说明 | 验证状态 |
|---|---|---|---|
| 1.0.0-draft.1 | 2026-11-15 | 将positive主题改为general_experience；独立主题/情感；限定重叠优先级；增加unknown与证据字段 | 设计文档核对，分类器未实现 |
| 1.0.0-draft.2 | 2026-11-15 | 局部审核完善：区分invalid_input与正常unknown，登记唯一C/S规则ID，补充独立多主题判定、状态和原因字段、边界案例；原15条及E01—E22不变 | 设计文档核对，分类器未实现 |
| 1.0.0-draft.3 | 2026-11-15 | 修正配网指引候选证据；增加兼容规则显式固定排序及全部命中证据保留约定；原始15条及E/B案例不变 | 设计文档核对，分类器未实现 |
| 1.0.0 | 2026-11-15 | 最终结构修订：独立matched_rules及原文位置，ambiguity_reasons仅保留语义歧义；冻结规则契约进入阶段2.1 | 实现及验证见阶段2.1交付报告，待用户验收 |

## 11. 额外近义表达与边界测试案例

以下为人工设计的未来验收案例，未运行自动分类测试。前12条为近义/相关表达，后续为冲突与拒绝分类边界；不修改前述15条原始文本。

| ID | 输入文本 | 预期主题 | 预期情感 | 依据/边界 |
|---|---|---|---|---|
| E01 | 设备总是掉线，得反复重连。 | device_connection | negative | 设备掉线与重连 |
| E02 | 设备连接很稳定，用着放心。 | device_connection | positive | 连接主题可以是正面 |
| E03 | 配网过程太复杂了，请补一份操作说明。 | device_connection | negative | 同一配网对象的具体重叠规则 |
| E04 | 刚注册完，我就不知道接下来该做什么。 | onboarding | negative | 注册后下一步不清楚 |
| E05 | 新手教程很清楚，第一次用也能上手。 | onboarding | positive | 明确新手场景与表扬 |
| E06 | 建议加一段首次使用教程。 | onboarding | neutral | 仅建议 |
| E07 | 常用功能的入口太难找了。 | feature_discovery | negative | 难找是明确体验不足，区别于未注意到 |
| E08 | 建议把常用功能放到首页。 | feature_discovery | neutral | 入口改进建议 |
| E09 | 打开页面等很久，加载速度太慢。 | performance | negative | 页面加载慢 |
| E10 | 每次操作都要等半天才响应。 | performance | negative | 响应耗时 |
| E11 | 应用运行很流畅，操作响应也很快。 | performance | positive | 同一性能主题、明确肯定 |
| E12 | 希望应用运行得更流畅些。 | performance | neutral | 无明确问题的建议 |
| E13 | 总体体验很棒，日常用着挺方便。 | general_experience | positive | 无更具体对象的总体评价 |
| E14 | 整体体验很差，操作流程也不好用。 | general_experience | negative | general_experience不等于正面 |
| E15 | 设备一直掉线，而且页面加载很慢。 | unknown | negative | 两个独立主题，不按排序强行选 |
| E16 | 设备连接很稳定，但偶尔也会断开。 | device_connection | unknown | 同一主题有明确正负冲突 |
| E17 | 配网很容易，但是首次注册的下一步指引很差。 | unknown | unknown | 两个独立主题及明确正负冲突 |
| E18 | 以前页面加载慢，现在很好用。 | performance | unknown | 时间转折首版未消解，保留冲突 |
| E19 | 价格太贵了。 | unknown | negative | 无价格主题，但明确抱怨 |
| E20 | 今天天气怎么样？ | unknown | unknown | 不属于已支持反馈语义 |
| E21 | （输入为空字符串或仅空白） | unknown | unknown | 无有效内容 |
| E22 | 设备连接不算不稳定吧。 | device_connection | unknown | 未支持的复杂否定 |

后续阶段须同时测试规则证据、候选标签、is_ambiguous、冲突原因与版本，而非只比较最终标签。

### 追加审核边界案例（保留E01—E22不变）

以下B01—B08追加验证八种审核场景；仍为设计预期，并非测试执行结果。

| ID | 文本 | 主题 / 情感 | classification_status | 规则ID（C / S） | 未知原因与证据 |
|---|---|---|---|---|---|
| B01 | 设备连接稳定，重连也很顺利。 | device_connection / positive | classified | C.DEVICE.CONNECTION / S.POSITIVE.PRAISE | 原因均null；设备连接、稳定、顺利 |
| B02 | 设备连接失败，反复断线。 | device_connection / negative | classified | C.DEVICE.CONNECTION / S.NEGATIVE.PROBLEM | 原因均null；设备连接、失败、断线 |
| B03 | 页面加载很快，体验很好。 | performance / positive | classified | C.PERFORMANCE.LOAD / S.POSITIVE.PRAISE | 原因均null；页面加载、很快、很好 |
| B04 | 页面加载太慢，还经常卡住。 | performance / negative | classified | C.PERFORMANCE.LOAD / S.NEGATIVE.PROBLEM | 原因均null；页面加载、太慢、卡住 |
| B05 | 首页的功能入口很清楚，常用功能很容易找到。 | feature_discovery / positive | classified | C.FEATURE.ENTRY / S.POSITIVE.PRAISE | 原因均null；功能入口、很清楚、容易找到 |
| B06 | 建议增加一个功能介绍入口。 | feature_discovery / neutral | classified | C.FEATURE.ENTRY / S.NEUTRAL.SUGGESTION | 原因均null；功能介绍入口、建议增加；同一对象，兼容介绍规则 |
| B07 | 设备连接失败，而且页面加载太慢。 | unknown / negative | partially_classified | C.UNKNOWN.MULTIPLE_TOPICS / S.NEGATIVE.PROBLEM | 主题原因multiple_independent_topics，情感原因null；候选device_connection/performance，分别有连接失败/加载太慢证据 |
| B08 | 设备连接很稳定，但也经常断线。 | device_connection / unknown | partially_classified | C.DEVICE.CONNECTION / S.UNKNOWN.CONFLICT | 主题原因null，情感原因conflicting_sentiment；情感候选positive/negative，证据稳定/断线 |

B07、B08的is_ambiguous=true且conflict_reasons非空，分别记录主题/情感冲突；B01—B06没有冲突，is_ambiguous=false。原文“建议”不单独决定情感，必须确认没有其他明确评价。

## 12. 本阶段边界与待解决问题

阶段1文档本身只规定设计预期，不将其当作运行结果。用户已授权最终结构修订后进入阶段2.1分类引擎；不扩展数据库查询和业务分析函数。

待阶段2解决：将规则ID转换成可执行的关键词组合/短语匹配表，定义否定匹配范围和分句办法，验证真实独立多主题与同对象重叠的区分，验证全部案例和未知文本的保守回退。若发现设计案例无法由明确规则可靠支持，应修订设计并说明原因，不得为了通过样例硬编码整个句子映射。

仍有业务限制：改进建议的neutral口径不等于满意；当前只有15种不同原文，覆盖不了复杂自然语言；反讽、跨句指代、历史与当前体验冲突尚未支持；不增加价格等新主题；不从连接反馈推断技术根因。

阶段2.1仅实现和测试纯文本分类引擎，完成后停止等待用户验收；不继续数据库查询、业务分析函数或CLI阶段。

### 阶段2.1实现与验证说明

实现位于src/data/feedback_classifier/，公开入口为classify_feedback_text(text)。rules.py登记不可变规则及显式match_kind/priority；normalizer.py规范化副本并映射原文位置；category_classifier.py与sentiment_classifier.py分别匹配；conflict_resolver.py保守判断冲突；__init__.py组装结构化结果。没有数据库或网络访问。

实现以有界短语/关键词正则匹配作为首版策略，不能完整理解自然语言。独立多主题检查基于不同对象的分离证据和分句中的评价/问题/请求信号；不足以确认独立命题时返回ambiguous_topic。否定表扬词不作肯定；未覆盖的否定故障表达保守返回unsupported_context，不把“没有掉线”错误判成故障。字符规范化按逐字符NFKC和空白折叠处理，不承诺跨字符Unicode组合等价。

验证：test_feedback_classifier.py共25个测试方法通过，其中逐项覆盖15种原文、E01—E22和B01—B08共45条样例；另覆盖原文索引、invalid_input、冲突证据、规则顺序反转及重复调用隔离。按原始频次加权测试合计186，主题/情感分布与设计一致；这是测试夹具校验，不是本阶段正式数据库分析。旧工具回归激活12、留存8、活跃11项均通过。沙箱临时目录权限导致的首次回归错误，在沙箱外重跑现有临时数据库测试后消除。
