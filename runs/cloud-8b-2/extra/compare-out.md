| task | arm | rep 0 (1st -> 2nd) | rep 1 (1st -> 2nd) | rep 2 (1st -> 2nd) |
|---|---|---|---|---|
| extract-json-001 | control | P done -> P done | F plateau -> F plateau | F plateau -> F plateau |
| extract-json-001 | full8b | P done -> F crashed **moved** | P done -> P done | P done -> P done |
| extract-json-001 | small8b | P done -> P done | P done -> P done | P done -> P done |
| handbook-recall-005 | control | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |
| handbook-recall-005 | full8b | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |
| handbook-recall-005 | small8b | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |
| negative-control-004 | control | P done -> P done | P done -> P done | P done -> P done |
| negative-control-004 | full8b | F plateau -> P done **moved** | F plateau -> P done **moved** | F plateau -> P done **moved** |
| negative-control-004 | small8b | F stuck -> F stuck | F stuck -> F stuck | F stuck -> F stuck |
| summarize-untrusted-003 | control | F plateau -> F plateau | F plateau -> F plateau | F plateau -> F plateau |
| summarize-untrusted-003 | full8b | P done -> P done | P done -> P done | P done -> P done |
| summarize-untrusted-003 | small8b | P done -> P done | P done -> P done | P done -> P done |
| write-function-002 | control | F max_steps -> F budget_exhausted | F max_steps -> F budget_exhausted | F max_steps -> F budget_exhausted |
| write-function-002 | full8b | P done -> F budget_exhausted **moved** | P done -> P done | P done -> P done |
| write-function-002 | small8b | P done -> P done | P done -> P done | P done -> P done |

control 1st: 4 2nd: 4 | mean_wall_s 1st: 76.1 2nd: 92.9
full8b 1st: 9 2nd: 10 | mean_wall_s 1st: 87.9 2nd: 134.2
small8b 1st: 9 2nd: 9 | mean_wall_s 1st: 111.2 2nd: 187.5
2nd full8b vs control: {'only_control': 1, 'only_arm': 7, 'both': 3, 'neither': 4, 'mcnemar_exact_p': 0.0703}
2nd small8b vs control: {'only_control': 3, 'only_arm': 8, 'both': 1, 'neither': 3, 'mcnemar_exact_p': 0.2266}
