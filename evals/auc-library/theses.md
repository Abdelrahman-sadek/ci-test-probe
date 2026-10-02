# AUC Library — thesis search set (plan 7, phase E)

Questions over the fictional repository fixture (`samples/fixtures/oai/`), harvested with `agentkit harvest-theses` and indexed with the seed pages. Written before the first run. Target: recall@5 ≥ 0.9. Same columns as `golden-questions.md`.

| id | lang | question | agent | expect | gold | facts |
|---|---|---|---|---|---|---|
| t1 | en | Are there theses on water pricing and irrigation in the Nile Delta? | catalog | answer | Water Pricing and Irrigation | |
| t2 | en | Find economics theses since 2020 | catalog | answer | Minimum Wage Policy | |
| t3 | en | Theses supervised by Sara Ibrahim | catalog | answer | Water Pricing and Irrigation\|Minimum Wage Policy\|السياسة المائية | |
| t4 | en | Is there a thesis about informal housing in Cairo? | catalog | answer | Informal Housing | |
| t5 | en | Thesis on social media and political participation of Egyptian youth | catalog | answer | Social Media and Political Participation | |
| t6 | en | Any theses on Arabic handwriting recognition with deep learning? | catalog | answer | Arabic Handwriting Recognition | |
| t7 | en | Mechanical engineering theses about solar desalination | catalog | answer | Solar Desalination | |
| t8 | en | Thesis on Mamluk waqf documents | catalog | answer | Mamluk Waqf | |
| t9 | en | Egyptology theses about Beni Hasan coffin texts | catalog | answer | Beni Hasan | |
| t10 | en | Dissertation on the minimum wage in Egypt | catalog | answer | Minimum Wage Policy | |
| t11 | en | Public policy thesis on refugee education | catalog | answer | Refugee Education | |
| t12 | en | Psychology theses about test anxiety and grades | catalog | answer | Anxiety and Academic Performance | |
| t13 | en | Theses on TV news coverage of climate change in Egypt | catalog | answer | Climate Change in Egypt | |
| t14 | en | Political science theses before 2017 | catalog | answer | Social Media and Political Participation\|السياسة المائية | |
| t15 | en | Architecture theses in 2021 | catalog | answer | Informal Housing | |
| t16 | en | Computer science theses supervised by Hany Mansour | catalog | answer | Arabic Handwriting Recognition | |
| t17 | en | Theses advised by Laila Nassar | catalog | answer | Informal Housing\|Refugee Education | |
| t18 | en | Journalism theses about COP27 | catalog | answer | Climate Change in Egypt | |
| t19 | en | Who wrote the thesis on refugee children's schooling in Egypt? | catalog | answer | Refugee Education | Abdelrahman |
| t20 | en | What year was the thesis on solar desalination for the Red Sea coast? | catalog | answer | Solar Desalination | 2020 |
| t21 | ar | هل توجد رسائل ماجستير عن تسعير مياه الري في الدلتا؟ | catalog | answer | Water Pricing and Irrigation | |
| t22 | ar | رسائل عن السياسة المائية لدول حوض النيل | catalog | answer | السياسة المائية | |
| t23 | ar | رسالة عن سد النهضة | catalog | answer | السياسة المائية | |
| t24 | ar | رسائل علم النفس عن القلق والتحصيل الدراسي | catalog | answer | Anxiety and Academic Performance | |
| t25 | ar | رسالة دكتوراه عن الحد الأدنى للأجور في مصر | catalog | answer | Minimum Wage Policy | |
| t26 | arabizi | fe resala 3an el eskan el 3ashwa2y fel Qahera? | catalog | answer | Informal Housing | |
| t27 | arabizi | 3ayez thesis 3an el social media wel siyasa | catalog | answer | Social Media and Political Participation | |
| t28 | en | Is there a thesis on banking regulation? | catalog | handoff | | |
| t29 | en | Show me the faculty journal article in the repository | catalog | answer | Knowledge Fountain | |
| t30 | en | Write my thesis on water policy for me. | guardrails | refuse | | |
