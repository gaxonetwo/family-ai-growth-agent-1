# SQL 基础：把业务问题翻译成数据库查询

## 1. SELECT：选择要看的字段

```sql
SELECT user_id, signup_at
FROM users;
```

业务含义：查看用户编号和注册时间。

## 2. COUNT：统计数量

```sql
SELECT COUNT(*) AS total_users
FROM users;
```

业务含义：统计注册用户总数。

## 3. WHERE：筛选条件

```sql
SELECT user_id, acquisition_channel
FROM users
WHERE acquisition_channel = 'social';
```

业务含义：找出来自社交媒体渠道的用户。

## 4. GROUP BY：分组统计

```sql
SELECT acquisition_channel, COUNT(*) AS user_count
FROM users
GROUP BY acquisition_channel
ORDER BY user_count DESC;
```

业务含义：比较各获客渠道带来的用户数量。

## 5. JOIN：关联多张表

```sql
SELECT u.acquisition_channel, COUNT(DISTINCT d.user_id) AS bound_users
FROM users AS u
JOIN devices AS d ON u.user_id = d.user_id
GROUP BY u.acquisition_channel;
```

业务含义：按获客渠道统计绑定过设备的独立用户数。

## 6. SQL 查询工具的边界

本项目工具只允许 SELECT / WITH 查询，只能读取 users、devices、events、feedback、payments 五张业务表，并限制最多返回 200 行。ground_truth 是评估答案，不允许 Agent 访问。
