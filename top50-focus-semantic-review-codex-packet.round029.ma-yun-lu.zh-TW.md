# Codex 句級關係語意審查包

- 產生時間：`2026-05-26T16:07:08+00:00`
- 使用 skill：`integrations/codex-skills/sanguo-relationship-semantic-review/SKILL.md`
- 待審句子數：`120`
- 輸出 JSONL：`artifacts/data-pipeline/sanguo-rag/extracted/baihua-bootstrap/wave-001-max1200/codex-skill-review/codex-relationship-semantic-reviewed-cache.jsonl`
- 原則：只根據原文句子判斷，不憑記憶補事實；不確定就寫 `not_enough_context`。
- 原則：`canonicalWrites=false`；這只是 evidence/proposal，不直接寫正式關係白名單。

## 輸出要求

請依 `sanguo-relationship-semantic-review` skill，為每個 `entries[]` 產生一行 reviewed cache JSONL。

## 待審項目摘要

### 1. `relsem.07071ae06d0169be5c99354a`

- 原文：賈詡沒有直接回答而是提到了袁紹和劉表父子因為廢長立幼而導致的內亂。
- 候選：parent_child:yuan-shao->liu-biao; parent_child:liu-biao->yuan-shao

### 2. `relsem.0358e0d9572135c234ecc667`

- 原文：孔明要斬關羽，劉備說：“從前我們三個人結義，說不能同日生，但願同日死。
- 候選：sworn_sibling:liu-bei->zhuge-liang; sworn_sibling:guan-yu->zhuge-liang

### 3. `relsem.6d5213f5e2b90d13c3fdc418`

- 原文：# 第九十三回 薑伯約歸降孔明 武鄉侯罵死王朝
- 候選：ruler_subject:zhuge-liang->jiang-wei; ruler_subject:jiang-wei->zhuge-liang

### 4. `relsem.565710c9664c2739718fa172`

- 原文：# 第五十五回 劉備智激孫夫人 孔明二氣周公瑾
- 候選：spouse:liu-bei->sun-shang-xiang; spouse:sun-shang-xiang->zhuge-liang; spouse:sun-shang-xiang->zhou-yu

### 5. `relsem.abfdaee664d2a17b85a79498`

- 原文：呂布又讓自己的妻子兒女來拜見劉備。
- 候選：spouse:liu-bei->lu-bu

### 6. `relsem.90506dd8fc8bbf68405d58c9`

- 原文：他先前娶了袁紹的次子袁熙的妻子甄氏為夫人，這是在攻破鄴城時得到的。
- 候選：spouse:yuan-shao->zhen-ji

### 7. `relsem.c28b66069778bca46ca11f63`

- 原文：孔明此時正在帳中與馬謖、呂凱、蔣琬、費禕等人共同商議平定南蠻的事宜忽然聽到帳下有人報告說孟獲派遣弟弟孟優來進獻寶貝。
- 候選：ruler_subject:zhuge-liang->meng-huo; ruler_subject:meng-huo->zhuge-liang

### 8. `relsem.45f6f9dd3519335fc203573a`

- 原文：青州刺史袁譚是袁紹的長子，聽說劉備來了，立刻開門出迎，然後護送劉備向北去了冀州。
- 候選：ruler_subject:liu-bei->yuan-shao; ruler_subject:yuan-shao->liu-bei; parent_child:liu-bei->yuan-shao; parent_child:yuan-shao->liu-bei

### 9. `relsem.95a24be4c5280175bc656921`

- 原文：馬雲騄在小說中的丈夫是趙雲。
- 候選：spouse:ma-yun-lu->zhao-yun

### 10. `relsem.b55b1322a8011a154118bfb0`

- 原文：劉備一看孫夫人的侍女都帶著刀劍，不由得嚇得臉色一變。
- 候選：spouse:liu-bei->sun-shang-xiang

### 11. `relsem.e89da96ceb50d0f5def9cf1d`

- 原文：隨後劉備和孫夫人相敬如賓，吳國太知道了也很高興。
- 候選：spouse:liu-bei->sun-shang-xiang

### 12. `relsem.dd763f787ab9e40e574d5c3a`

- 原文：張遼說道：現在曹操已經派兵奪取了下邳，城中有劉備的兩位夫人，都被曹操派兵保護好了，特來告訴兄長。
- 候選：spouse:cao-cao->liu-bei

### 13. `relsem.80e02310eb873191599299be`

- 原文：劉備聽見後麵喊聲大起，趕緊告訴孫夫人。
- 候選：spouse:liu-bei->sun-shang-xiang

### 14. `relsem.bb21f1da3ddd3778f5e6ab4d`

- 原文：劉表的夫人蔡氏在屏風後麵，聽了劉備這話，心中分外怨恨。
- 候選：spouse:liu-bei->liu-biao

### 15. `relsem.f5e88be7d8f047d4e601637e`

- 原文：第二天，祝融夫人的弟弟帶來洞主，把孟獲和祝融夫人和一些親戚，送到孔明營中，說自己勸孟獲投降，孟獲不答應，所以抓了他們送來。
- 候選：spouse:zhu-rong-furen->zhuge-liang; spouse:meng-huo->zhu-rong-furen; sibling:meng-huo->zhu-rong-furen

### 16. `relsem.1622756c6cbe0140b7e47770`

- 原文：孔明命人衝上去，抓了孟獲和祝融夫人，從身上搜出兵器。
- 候選：spouse:zhu-rong-furen->zhuge-liang; spouse:meng-huo->zhu-rong-furen

### 17. `relsem.2fc0e6762a4da78274139eb5`

- 原文：馬雲騄是馬超之妹，後與趙雲成婚。
- 候選：spouse:ma-yun-lu->zhao-yun

### 18. `relsem.63af2db39fce923190bbc305`

- 原文：貂蟬對呂布說：“我今生不能跟你做夫妻，就盼著下輩子再在一起吧。
- 候選：spouse:diao-chan->lu-bu

### 19. `relsem.aa456010347fa3453bc4b459`

- 原文：孟獲的妻子祝融夫人，善於使用飛到，後背背著無知非同，手持丈八長標，出洞迎戰。
- 候選：spouse:meng-huo->zhu-rong-furen

### 20. `relsem.ee81eda6e602ab9eb990b8c3`

- 原文：孫乾進城見到張飛，說：“關公從許都送二位夫人來到這裏，要去找劉皇叔，請將軍出城迎接一下吧。
- 候選：spouse:sun-qian->zhang-fei

### 21. `relsem.f1c1a6207f786057ec24c142`

- 原文：馬雲騄是馬騰之女，與趙雲成婚。
- 候選：spouse:ma-yun-lu->zhao-yun

### 22. `relsem.d0a9faba2f2d168d5e112bdc`

- 原文：酒宴散了之後，過了幾天，劉備和孫夫人完婚，大排筵會，到了晚上客人才散去。
- 候選：spouse:liu-bei->sun-shang-xiang

### 23. `relsem.ae9c1652b8b8d68d9d6fc18e`

- 原文：張飛和趙雲商議：“如果逼死夫人，也不符合臣子的規矩，就帶著阿鬥回去吧。
- 候選：spouse:zhang-fei->zhao-yun

### 24. `relsem.e45940e741041f4574a0be94`

- 原文：魏延上前挑戰，詐敗逃走，祝融夫人追趕，被馬岱用絆馬索絆倒，捉住了，送到了孔明那裏。
- 候選：spouse:wei-yan->zhu-rong-furen; spouse:ma-dai->zhu-rong-furen

### 25. `relsem.db5a7b2f1236e0c810358eb4`

- 原文：周善見了，提刀來迎，被張飛手起一劍砍倒，把人頭砍下，扔在孫夫人跟前。
- 候選：spouse:sun-shang-xiang->zhang-fei

### 26. `relsem.a3ea6bacf6b9f9481d177964`

- 原文：”張遼又說：“要把劉備的俸祿發給劉備的兩位夫人，而且外人不許上門。
- 候選：spouse:liu-bei->zhang-liao

### 27. `relsem.3fa6df3b2e99bd98fe179744`

- 原文：隨即關羽進下邳城，把劉備的兩位夫人——甘夫人、糜夫人接上，跟著曹操的軍隊，一起回了許都。
- 候選：spouse:cao-cao->liu-bei

### 28. `relsem.8a31b7b464049b7e16113d4b`

- 原文：周善剛要開船，趙雲在城裏聽說孫夫人走了，這時追了出來，大叫：“不要開船，等我送一下夫人。
- 候選：spouse:sun-shang-xiang->zhao-yun

### 29. `relsem.3943587b53ed7d93b6b87f4d`

- 原文：劉備於是和孫夫人一起去找吳國太，說：“我的父母祖墳，都在涿郡，最近很是想念，想去江北，遙遙祭祀一下。
- 候選：spouse:liu-bei->sun-shang-xiang

### 30. `relsem.d6f9e09f61db002ad6fe64f1`

- 原文：荀彧說：“關羽保護著劉備的夫人，守衛這個城，我們得抓緊進攻，免得袁紹來救。
- 候選：spouse:guan-yu->liu-bei; spouse:liu-bei->yuan-shao

### 31. `relsem.8fdeb5c56905ca5362ddfa85`

- 原文：孔明放了祝融夫人，換回了張嶷、馬忠。
- 候選：spouse:zhu-rong-furen->zhuge-liang

### 32. `relsem.e88f3065175a8beb27c23477`

- 原文：趙雲和孫夫人等著後麵四員將官來到，到了之後，孫夫人說：“陳武、潘璋，你們來幹什麽？
- 候選：spouse:sun-shang-xiang->zhao-yun

### 33. `relsem.7256a3fc87b1f9a6d7cdc55c`

- 原文：孫堅有四個兒子，都是吳夫人生的：長子孫策，字伯符；次子孫權，字仲謀；三子孫翊，字叔弼；四子孫匡，字季佐。
- 候選：parent_child:sun-quan->sun-ce; parent_child:sun-ce->sun-quan; spouse:sun-ce->sun-quan

### 34. `relsem.9cf37f5f06d03ddfa5dfd965`

- 原文：”說完，報著阿鬥，和趙雲回船，放孫夫人的船隻走了。
- 候選：spouse:sun-shang-xiang->zhao-yun

### 35. `relsem.ce2df9ca2cd21cf3cca909de`

- 原文：”孫夫人大怒說：“周瑜真是逆賊，我東吳不曾虧負你！
- 候選：spouse:sun-shang-xiang->zhou-yu

### 36. `relsem.e0cbf60f8ebdba4b30980e61`

- 原文：孫夫人說：“你先走，我和子龍等他們。
- 候選：spouse:sun-shang-xiang->zhao-yun

### 37. `relsem.5873de118acf953b017df670`

- 原文：”瑜說：“你不知道，大喬是孫策的夫人，小喬是我的夫人。
- 候選：spouse:sun-ce->xiao-qiao; spouse:da-qiao->sun-ce

### 38. `relsem.b163c96c3b1d2e73785a59a8`

- 原文：”孫夫人斥責說：“你隻怕周瑜，就不怕我？
- 候選：spouse:sun-shang-xiang->zhou-yu

### 39. `relsem.618e66aa878b660ff139eebe`

- 原文：”劉備說：“子龍真大丈夫也！
- 候選：spouse:liu-bei->zhao-yun

### 40. `relsem.a5b41dc2b692ab1638f10840`

- 原文：”孔明說：“曹操而兒子曹植，字子建，很會寫詩。
- 候選：parent_child:zhuge-liang->cao-cao; parent_child:cao-cao->zhuge-liang

### 41. `relsem.b8a8d6bab7286a6756118cf1`

- 原文：但是我們袁紹大將軍西討董卓的時候，覺得他多少有點才能，就錄用他做了部下將官。
- 候選：ruler_subject:yuan-shao->dong-zhuo; ruler_subject:dong-zhuo->yuan-shao

### 42. `relsem.ea5dd9933c28c1b80285a111`

- 原文：袁紹的弟弟袁術在淮南虐待百姓，於是民眾都叛亂了。
- 候選：sibling:yuan-shao->yuan-shu

### 43. `relsem.6ad94f0a4c769c9bdf2a91da`

- 原文：現在我們想舍棄一死拼殺孟獲去投奔孔明以免洞中百姓遭受塗炭之苦。
- 候選：ruler_subject:zhuge-liang->meng-huo; ruler_subject:meng-huo->zhuge-liang

### 44. `relsem.aefc414a1d1e0d565398b823`

- 原文：諸葛亮為趙雲做媒人，迎娶了馬超智勇雙全的妹妹馬雲騄，令其雙雙建功立業。
- 候選：sibling:ma-chao->ma-yun-lu

### 45. `relsem.7fea3abb475d144e8c3ddb53`

- 原文：桂陽太守趙範也是曹操任命的，同樣隻得下城投降，把趙雲接入城中。
- 候選：ruler_subject:cao-cao->zhao-yun; ruler_subject:zhao-yun->cao-cao

### 46. `relsem.0f1bfb198f71fc6e52fc74a5`

- 原文：冀州牧袁紹作為州郡長官的盟主，就上表朝廷，申請封曹操做兗州的東郡太守，去抵擋黃巾。
- 候選：ruler_subject:cao-cao->yuan-shao; ruler_subject:yuan-shao->cao-cao

### 47. `relsem.0742a49eb0b3a7fad01eabc2`

- 原文：” 呂布曾經背叛和殺死並州刺史丁原和董卓，劉備說的這句話就是告訴曹操，不能信任呂布。
- 候選：ruler_subject:liu-bei->cao-cao; ruler_subject:cao-cao->liu-bei; ruler_subject:liu-bei->dong-zhuo; ruler_subject:dong-zhuo->liu-bei; ruler_subject:liu-bei->lu-bu; ruler_subject:lu-bu->liu-bei

### 48. `relsem.0742a49eb0b3a7fad01eabc2.part002`

- 原文：” 呂布曾經背叛和殺死並州刺史丁原和董卓，劉備說的這句話就是告訴曹操，不能信任呂布。
- 候選：ruler_subject:cao-cao->dong-zhuo; ruler_subject:dong-zhuo->cao-cao; ruler_subject:cao-cao->lu-bu; ruler_subject:lu-bu->cao-cao; ruler_subject:lu-bu->dong-zhuo; ruler_subject:dong-zhuo->lu-bu

### 49. `relsem.52aa3788b693a38b796121f5`

- 原文：等關羽和曹仁交兵，主公可以暗自派出一員將軍，偷著攻取荊州。
- 候選：ruler_subject:guan-yu->cao-ren; ruler_subject:cao-ren->guan-yu

### 50. `relsem.8bf27a9dcf84f0f33ba7405d`

- 原文：呂布一看袁術篡位稱皇帝，屬於大逆不道，於是拒絕了這樁婚事。
- 候選：ruler_subject:lu-bu->yuan-shu; ruler_subject:yuan-shu->lu-bu

### 51. `relsem.196904759c62529659355aa6`

- 原文：孟獲回到本寨後，先埋伏下刀斧手在帳下，然後派遣心腹人到董荼那、阿會喃的寨中，假傳孔明有使命到來，將二人騙到大寨帳下殺死，棄屍於澗中。
- 候選：ruler_subject:zhuge-liang->meng-huo; ruler_subject:meng-huo->zhuge-liang

### 52. `relsem.58aeaef3cb1fe607079954dd`

- 原文：周瑜的部將都想殺孔明，但是看趙雲在旁邊跟著，不敢下手。
- 候選：ruler_subject:zhuge-liang->zhao-yun; ruler_subject:zhao-yun->zhuge-liang; ruler_subject:zhuge-liang->zhou-yu; ruler_subject:zhou-yu->zhuge-liang; ruler_subject:zhao-yun->zhou-yu; ruler_subject:zhou-yu->zhao-yun

### 53. `relsem.8826c872e683d39c57f59bab`

- 原文：經過袁紹軍的營寨時，守兵問是什麽軍隊，曹操就派人用河北口音回答說：“蔣奇奉命去烏巢護糧。
- 候選：ruler_subject:cao-cao->yuan-shao; ruler_subject:yuan-shao->cao-cao

### 54. `relsem.ab6d8cb2d319c410f19a0077`

- 原文：曹丕剛剛當了皇帝，見孫權就來歸降稱臣，大喜，覺得自己比父親曹操還厲害。
- 候選：ruler_subject:cao-cao->sun-quan; ruler_subject:sun-quan->cao-cao

### 55. `relsem.384522dc8858e4ab2cd61ec2`

- 原文：劉備來了以後，就親自到黃忠家來請，黃忠這才歸降劉備。
- 候選：ruler_subject:liu-bei->huang-zhong; ruler_subject:huang-zhong->liu-bei

### 56. `relsem.6ece4cfe2df1b23c5a0f7cce`

- 原文：趙雲見趙範跑了，於是留部將守城，自己回去向劉備匯報。
- 候選：ruler_subject:liu-bei->zhao-yun; ruler_subject:zhao-yun->liu-bei

### 57. `relsem.cfb95fefea4c2439aae47958`

- 原文：龐統對劉備說：“明天主公和劉璋在筵席上相見，可以趁機殺了劉璋，這樣就可以得到益州了。
- 候選：ruler_subject:liu-bei->pang-tong; ruler_subject:pang-tong->liu-bei

### 58. `relsem.cf3349f09d66499844e78f8b`

- 原文：隨後，張郃在山頂觀望，就見張飛坐在帳下喝酒，叫士兵在前麵相撲表演。
- 候選：ruler_subject:zhang-fei->zhang-he; ruler_subject:zhang-he->zhang-fei

### 59. `relsem.272826565bb04b2db34cf340`

- 原文：主公可以派人勸曹操，叫曹仁南下。
- 候選：ruler_subject:cao-cao->cao-ren; ruler_subject:cao-ren->cao-cao

### 60. `relsem.cdf107e73918f590be2d32d8`

- 原文：主公去見袁紹，說要去荊州，勸說劉表一起打曹操，就可以乘機離去了。
- 候選：ruler_subject:cao-cao->liu-biao; ruler_subject:liu-biao->cao-cao; ruler_subject:cao-cao->yuan-shao; ruler_subject:yuan-shao->cao-cao; ruler_subject:yuan-shao->liu-biao; ruler_subject:liu-biao->yuan-shao

### 61. `relsem.a947ac098cc77d955ed21189`

- 原文：那裏是夏侯淵的部將杜襲把守，隻有幾百人，見黃忠殺來，隻得逃走。
- 候選：ruler_subject:huang-zhong->xiahou-yuan; ruler_subject:xiahou-yuan->huang-zhong

### 62. `relsem.ee807d08ef908dde0cbf1fd2`

- 原文：張昭說：“如今主公殺了孫權，就怕劉備前來為他報仇，到時候東吳恐怕難以抵擋。
- 候選：ruler_subject:liu-bei->sun-quan; ruler_subject:sun-quan->liu-bei

### 63. `relsem.404b46c66bb8dea049ff62d3`

- 原文：”原來，龐統本是周瑜屬下的功曹，曾經在赤壁之戰巧獻連環計。
- 候選：ruler_subject:zhou-yu->pang-tong; ruler_subject:pang-tong->zhou-yu

### 64. `relsem.67e7292d3633f641b985f89c`

- 原文：袁術部將李豐舉槍來迎，戰不到三合，被呂布刺傷手，槍也丟了。
- 候選：ruler_subject:lu-bu->yuan-shu; ruler_subject:yuan-shu->lu-bu

### 65. `relsem.f1152ff16b64af37d62f99e5`

- 原文：張魯召集部將商議：“馬超剛剛敗了，曹操占據了官職，必然要南下奪我的漢中。
- 候選：ruler_subject:cao-cao->ma-chao; ruler_subject:ma-chao->cao-cao

### 66. `relsem.0f00bb74d659d6bb98e637c8`

- 原文：閻圃說：“龐德從前跟著馬超投奔主公，後來馬超投奔劉備，龐德得病沒有走。
- 候選：ruler_subject:liu-bei->ma-chao; ruler_subject:ma-chao->liu-bei

### 67. `relsem.3db5ce449e7358e84256b6da`

- 原文：”魏延想起孔明以前不聽他的計謀，也笑著說：“丞相當初如果聽我的，直接出子午穀，這時候別說長安，連洛陽都打下來了。
- 候選：ruler_subject:zhuge-liang->wei-yan; ruler_subject:wei-yan->zhuge-liang

### 68. `relsem.a02a3eaf9bd3497a6172668d`

- 原文：董衡說：“龐德原本的馬超的部將，現在馬超在劉備那裏，是劉備的五虎上將。
- 候選：ruler_subject:liu-bei->ma-chao; ruler_subject:ma-chao->liu-bei

### 69. `relsem.f19c35e09bde6640dd885d88`

- 原文：”曹操說：我一直喜歡關羽武藝高強，想收降他為我效力，不如派人去說降他。
- 候選：ruler_subject:cao-cao->guan-yu; ruler_subject:guan-yu->cao-cao

### 70. `relsem.94baf9477be5ea7a6663fa4d`

- 原文：”徐晃上前說：“丞相可以把大兵擺在潼關這裏，馬超必定也集中兵力守潼關，而黃河以西，一定沒有守備。
- 候選：ruler_subject:ma-chao->xu-huang; ruler_subject:xu-huang->ma-chao

### 71. `relsem.6b7d00a77c9a9664d2d57afc`

- 原文：黃忠說：“現在沒有龐統軍師，雒城又攻打不下，不如派人去荊州，把軍士諸葛亮請來商議。
- 候選：ruler_subject:zhuge-liang->huang-zhong; ruler_subject:huang-zhong->zhuge-liang; ruler_subject:zhuge-liang->pang-tong; ruler_subject:pang-tong->zhuge-liang; ruler_subject:huang-zhong->pang-tong; ruler_subject:pang-tong->huang-zhong

### 72. `relsem.8c50f3528027834d6a138fbd`

- 原文：孔明於是來見劉備，勸說：“文武官員一直追隨主公，都是為了能得到封賞。
- 候選：ruler_subject:liu-bei->zhuge-liang; ruler_subject:zhuge-liang->liu-bei

### 73. `relsem.c2c2497c9262d3c6dbedbb21`

- 原文：”於是拜龐統做副軍師中郎將，和孔明一起輔佐自己，操練士兵，準備攻伐。
- 候選：ruler_subject:zhuge-liang->pang-tong; ruler_subject:pang-tong->zhuge-liang

### 74. `relsem.3918c63699ff972c1b7a6452`

- 原文：馬超也連忙上馬，和龐德、馬岱匯合，一起迎戰韓遂的部將。
- 候選：ruler_subject:ma-chao->ma-dai; ruler_subject:ma-dai->ma-chao

### 75. `relsem.3d7e01429a803d2ddf434004`

- 原文：卻說趙雲從四更時分，跟曹軍廝殺，殺到天明，不見了劉備，也不見了劉備的家小，心想：“主公把甘、糜二夫人和小主人阿鬥，托付在我身上，現在失散了，有什麽麵目再去見主公？
- 候選：ruler_subject:liu-bei->zhao-yun; ruler_subject:zhao-yun->liu-bei

### 76. `relsem.08926a07289bf149480bb72a`

- 原文：這一日，忽有尚書鄧芝前來丞相府求見孔明，落座之後，鄧芝說：“現在皇帝年少，民眾尚未安定，而敵國強大，南蠻孟獲又進犯四郡。
- 候選：ruler_subject:zhuge-liang->meng-huo; ruler_subject:meng-huo->zhuge-liang

### 77. `relsem.ce1436ae930487ae1e591470`

- 原文：第二天，張飛又要出去挑戰，忽然一人上前說道：“馬超是當今虎將，如果與張將軍死戰，必有一傷，我有辦法，可以叫馬超歸降主公。
- 候選：ruler_subject:zhang-fei->ma-chao; ruler_subject:ma-chao->zhang-fei

### 78. `relsem.8031ca3aaebcb4e80deeac57`

- 原文：董卓這人野心很大，這時候他得到詔書大喜，立刻帶領軍馬，包括李傕、郭汜、張濟、樊稠等部將，向東朝著洛陽進發。
- 候選：ruler_subject:dong-zhuo->li-jue; ruler_subject:li-jue->dong-zhuo

### 79. `relsem.903ee615fbad6b449064b094`

- 原文：”龐統說：“主公不用回去，可以給劉璋發去書信，說孫權求救，我們不得不回兵去救孫權，怎奈一路要用的糧食不夠。
- 候選：ruler_subject:sun-quan->pang-tong; ruler_subject:pang-tong->sun-quan

### 80. `relsem.bf170f616378b1c9952c00dc`

- 原文：孔明召集諸將商議，正在交談，忽然門官來報，趙雲的長子趙統、次子趙廣，來見丞相。
- 候選：ruler_subject:zhuge-liang->zhao-yun; ruler_subject:zhao-yun->zhuge-liang

### 81. `relsem.0975369bdef88d719c69b1f2`

- 原文：”曹操問是什麽計策，賈詡說：“丞相可以給韓遂寫一封親筆信，中間一些字，可以塗抹掉重寫，然後送給韓遂，故意叫馬超知道。
- 候選：ruler_subject:cao-cao->ma-chao; ruler_subject:ma-chao->cao-cao

### 82. `relsem.4df89b83262a1a2377cade39`

- 原文：張魯正不知要不要去援救劉璋，就見馬超挺身出，說：“我感謝主公收留，無以報答，願領一隻軍馬，南下進攻葭萌關，生擒劉備。
- 候選：ruler_subject:liu-bei->ma-chao; ruler_subject:ma-chao->liu-bei

### 83. `relsem.1a37f1bbf52c07b94fab8649`

- 原文：”劉璋一聽，大驚失色，諸侯聽說是馬超歸降了劉備，一起來攻成都，全都又驚又怕，再不敢說抵抗。
- 候選：ruler_subject:liu-bei->ma-chao; ruler_subject:ma-chao->liu-bei

### 84. `relsem.ace27ae01e20a52b4b12efa0`

- 原文：這時呂布已經牽著馬到了門外，曹操一下子發慌，於是拿著刀跪下，說：“我有一口寶刀，獻給丞相。
- 候選：ruler_subject:cao-cao->lu-bu; ruler_subject:lu-bu->cao-cao

### 85. `relsem.ebcc38085ad8aa6d20092fd7`

- 原文：魯肅大怒說：“你要我們主公向曹操下跪？
- 候選：ruler_subject:cao-cao->lu-su; ruler_subject:lu-su->cao-cao

### 86. `relsem.fd8c73177266747a36c6150a`

- 原文：這時，劉禪發來詔書，褒獎諸葛亮先斬王雙，後得武都、陰平二郡，特恢複丞相職位。
- 候選：ruler_subject:zhuge-liang->liu-shan; ruler_subject:liu-shan->zhuge-liang

### 87. `relsem.9a2159ad8ed662b37bcff057`

- 原文：”魯肅說：“我把劉備的謀士諸葛孔明帶來了，主公可以問他，就知道曹操的虛實了。
- 候選：ruler_subject:liu-bei->cao-cao; ruler_subject:cao-cao->liu-bei; ruler_subject:liu-bei->lu-su; ruler_subject:lu-su->liu-bei; ruler_subject:liu-bei->zhuge-liang; ruler_subject:zhuge-liang->liu-bei

### 88. `relsem.9a2159ad8ed662b37bcff057.part002`

- 原文：”魯肅說：“我把劉備的謀士諸葛孔明帶來了，主公可以問他，就知道曹操的虛實了。
- 候選：ruler_subject:zhuge-liang->cao-cao; ruler_subject:cao-cao->zhuge-liang; ruler_subject:zhuge-liang->lu-su; ruler_subject:lu-su->zhuge-liang; ruler_subject:cao-cao->lu-su; ruler_subject:lu-su->cao-cao

### 89. `relsem.67e2f091cb8701913d9e6426`

- 原文：”趙雲說：“孔明軍師已經知道都督這是假途滅虢之計。
- 候選：ruler_subject:zhuge-liang->zhao-yun; ruler_subject:zhao-yun->zhuge-liang

### 90. `relsem.6fbf0e95b173be805a1a104f`

- 原文：”三人計議已定，第二天孫策去見袁術，哭著說：“現在我的舅舅吳景，被揚州刺史劉繇逼迫，丟了他的丹陽郡。
- 候選：ruler_subject:yuan-shu->sun-ce; ruler_subject:sun-ce->yuan-shu

### 91. `relsem.8b8e1e78df2e4e8038e70bec`

- 原文：又派李典、樂進向西去攻打並州，進攻並州刺史高幹——袁紹的女婿。
- 候選：ruler_subject:yuan-shao->li-dian; ruler_subject:li-dian->yuan-shao

### 92. `relsem.f5bdb046334d9cc8dda74091`

- 原文：回來之後，韓遂和五員將官商議，楊秋說：“馬超懷疑主公，不如投降曹操算了，還能得到封賞。
- 候選：ruler_subject:cao-cao->ma-chao; ruler_subject:ma-chao->cao-cao

### 93. `relsem.2bdc39bf2b3d261d8e07dd86`

- 原文：劉備跳下馬，拉住龐統的馬：“軍師這匹馬不太好啊？
- 候選：ruler_subject:liu-bei->pang-tong; ruler_subject:pang-tong->liu-bei

### 94. `relsem.6c8aba1c250c5d1074873aff`

- 原文：於是他下屬的吳郡太守守許貢，偷著給曹操寫信，說：“孫策驍勇，跟項羽一樣。
- 候選：ruler_subject:cao-cao->sun-ce; ruler_subject:sun-ce->cao-cao

### 95. `relsem.9c4458be9279086d522c7495`

- 原文：正往前走，山坡下又殺出兩支軍隊，是夏侯惇部將鍾縉、鍾紳兄弟，一個使大斧，一個使畫戟，大喝：“趙雲快下馬投降。
- 候選：ruler_subject:zhao-yun->xiahou-dun; ruler_subject:xiahou-dun->zhao-yun

### 96. `relsem.f1085d6b46cf6643cfac496e`

- 原文：走不多遠，就見迎麵來到一隻軍馬，旗上寫著“漢丞相諸葛亮”，中央一輛四輪車，孔明端坐車上，左有關興，右有張苞。
- 候選：ruler_subject:zhuge-liang->guan-xing; ruler_subject:guan-xing->zhuge-liang

### 97. `relsem.b6750def3b6e430654020e25`

- 原文：”張遼說：“劉備對關羽有情義，隻要丞相對他更有情義恩德，那還怕他不留下？
- 候選：ruler_subject:liu-bei->zhang-liao; ruler_subject:zhang-liao->liu-bei; ruler_subject:guan-yu->zhang-liao; ruler_subject:zhang-liao->guan-yu

### 98. `relsem.14d714f545fbe9b1b3c8637f`

- 原文：次日清晨，孫權升堂，文武都來了，周瑜當場說道：“曹操雖然名義是漢朝丞相，其實是漢賊。
- 候選：ruler_subject:cao-cao->sun-quan; ruler_subject:sun-quan->cao-cao; ruler_subject:cao-cao->zhou-yu; ruler_subject:zhou-yu->cao-cao; ruler_subject:sun-quan->zhou-yu; ruler_subject:zhou-yu->sun-quan

### 99. `relsem.8cc0b1796666524eae0ca551`

- 原文：曹軍來到城下，曹操說道：“龐德是西涼勇將，原是馬超的部將，我想收降他。
- 候選：ruler_subject:cao-cao->ma-chao; ruler_subject:ma-chao->cao-cao

### 100. `relsem.82d6f3efc5dd469f8d4b7372`

- 原文：等曹操上樓來了，呂布就說：“丞相所擔心的，不過就是我呂布。
- 候選：ruler_subject:cao-cao->lu-bu; ruler_subject:lu-bu->cao-cao

### 101. `relsem.cb4a00b9939926780cc6c03d`

- 原文：”劉禪應允，於是孔明叫蔣琬作參軍，費禕作丞相府長史，董厥、樊建做掾史，趙雲、魏延做大將，總督軍馬，王平、張翼為副將，帶著將士五十萬，向南朝益州郡出發。
- 候選：ruler_subject:zhuge-liang->liu-shan; ruler_subject:liu-shan->zhuge-liang; ruler_subject:zhuge-liang->wei-yan; ruler_subject:wei-yan->zhuge-liang; ruler_subject:zhuge-liang->zhao-yun; ruler_subject:zhao-yun->zhuge-liang

### 102. `relsem.cb4a00b9939926780cc6c03d.part002`

- 原文：”劉禪應允，於是孔明叫蔣琬作參軍，費禕作丞相府長史，董厥、樊建做掾史，趙雲、魏延做大將，總督軍馬，王平、張翼為副將，帶著將士五十萬，向南朝益州郡出發。
- 候選：ruler_subject:zhao-yun->liu-shan; ruler_subject:liu-shan->zhao-yun; ruler_subject:zhao-yun->wei-yan; ruler_subject:wei-yan->zhao-yun; ruler_subject:wei-yan->liu-shan; ruler_subject:liu-shan->wei-yan

### 103. `relsem.6838fae2a4ea0188a2aeb5f7.part010`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:xiahou-yuan->xu-zhu; ruler_subject:xu-zhu->xiahou-yuan

### 104. `relsem.6838fae2a4ea0188a2aeb5f7`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:cao-cao->cao-hong; ruler_subject:cao-hong->cao-cao; ruler_subject:cao-cao->cao-ren; ruler_subject:cao-ren->cao-cao; ruler_subject:cao-cao->li-dian; ruler_subject:li-dian->cao-cao

### 105. `relsem.6838fae2a4ea0188a2aeb5f7.part002`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:cao-cao->xiahou-dun; ruler_subject:xiahou-dun->cao-cao; ruler_subject:cao-cao->xiahou-yuan; ruler_subject:xiahou-yuan->cao-cao; ruler_subject:cao-cao->xu-huang; ruler_subject:xu-huang->cao-cao

### 106. `relsem.6838fae2a4ea0188a2aeb5f7.part003`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:cao-cao->xu-zhu; ruler_subject:xu-zhu->cao-cao; ruler_subject:cao-ren->cao-hong; ruler_subject:cao-hong->cao-ren; ruler_subject:cao-ren->li-dian; ruler_subject:li-dian->cao-ren

### 107. `relsem.6838fae2a4ea0188a2aeb5f7.part004`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:cao-ren->xiahou-dun; ruler_subject:xiahou-dun->cao-ren; ruler_subject:cao-ren->xiahou-yuan; ruler_subject:xiahou-yuan->cao-ren; ruler_subject:cao-ren->xu-huang; ruler_subject:xu-huang->cao-ren

### 108. `relsem.6838fae2a4ea0188a2aeb5f7.part005`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:cao-ren->xu-zhu; ruler_subject:xu-zhu->cao-ren; ruler_subject:xiahou-dun->cao-hong; ruler_subject:cao-hong->xiahou-dun; ruler_subject:xiahou-dun->li-dian; ruler_subject:li-dian->xiahou-dun

### 109. `relsem.6838fae2a4ea0188a2aeb5f7.part006`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:xiahou-dun->xiahou-yuan; ruler_subject:xiahou-yuan->xiahou-dun; ruler_subject:xiahou-dun->xu-huang; ruler_subject:xu-huang->xiahou-dun; ruler_subject:xiahou-dun->xu-zhu; ruler_subject:xu-zhu->xiahou-dun

### 110. `relsem.6838fae2a4ea0188a2aeb5f7.part007`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:xu-huang->cao-hong; ruler_subject:cao-hong->xu-huang; ruler_subject:xu-huang->li-dian; ruler_subject:li-dian->xu-huang; ruler_subject:xu-huang->xiahou-yuan; ruler_subject:xiahou-yuan->xu-huang

### 111. `relsem.6838fae2a4ea0188a2aeb5f7.part008`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:xu-huang->xu-zhu; ruler_subject:xu-zhu->xu-huang; ruler_subject:cao-hong->li-dian; ruler_subject:li-dian->cao-hong; ruler_subject:cao-hong->xiahou-yuan; ruler_subject:xiahou-yuan->cao-hong

### 112. `relsem.6838fae2a4ea0188a2aeb5f7.part009`

- 原文：曹操被封為大將軍武平侯，荀彧為尚書令，荀攸為軍師，郭嘉為司馬祭酒，劉曄為司空倉曹掾，毛玠、任峻為典農中郎將，負責督辦錢糧，程昱為東平相，範成、董昭為洛陽令，滿寵為許都令，夏侯惇、夏侯淵、曹仁、曹洪皆為將軍，呂虔、李典、樂進、於禁、徐晃皆為校尉，許褚、典韋皆為都尉；其餘將士，各個封官。
- 候選：ruler_subject:cao-hong->xu-zhu; ruler_subject:xu-zhu->cao-hong; ruler_subject:li-dian->xiahou-yuan; ruler_subject:xiahou-yuan->li-dian; ruler_subject:li-dian->xu-zhu; ruler_subject:xu-zhu->li-dian

### 113. `relsem.ba8a2cd046cb42d11ffdea1f`

- 原文：袁術得知消息，跟部下的都督張勳、紀靈、橋蕤，上將雷薄、陳芬等三十餘人商議，說：“孫策借我的兵起事，現在盡得江東地麵，怎麽辦？
- 候選：ruler_subject:yuan-shu->sun-ce; ruler_subject:sun-ce->yuan-shu

### 114. `relsem.e38d4ef65561de035fec96ea`

- 原文：分賓主落座後，魯肅問：“現在曹操要來，是戰是和，主公不能決定，全聽將軍的，將軍意見是怎樣？
- 候選：ruler_subject:cao-cao->lu-su; ruler_subject:lu-su->cao-cao

### 115. `relsem.902e72fa4ab8b9624b959fd9`

- 原文：”孫權回頭對文武說：“關羽是當今豪傑，我勸他歸降怎麽樣？
- 候選：ruler_subject:guan-yu->sun-quan; ruler_subject:sun-quan->guan-yu

### 116. `relsem.fafa2ef45fab2f73f625d2f7`

- 原文：”張昭說：“不如先派人把關羽首級，轉送給曹操，叫劉備知道，這是曹操叫主公敢的。
- 候選：ruler_subject:cao-cao->guan-yu; ruler_subject:guan-yu->cao-cao

### 117. `relsem.532c17fc66964b854a1cd9d4`

- 原文：”關羽說：“量一老卒，哪值一提，我不需要用三千兵，隻要本部下的五百名校刀手，就能斬黃忠、韓玄的首級。
- 候選：ruler_subject:guan-yu->huang-zhong; ruler_subject:huang-zhong->guan-yu

### 118. `relsem.10033f75e59afc63b1351672`

- 原文：魏延、陳式帶兵兩萬，朝北進軍，半路上，孔明派鄧芝追來，對二將說道：“丞相有令，行軍到了箕穀，要提防魏軍埋伏，不可輕易前進。
- 候選：ruler_subject:zhuge-liang->wei-yan; ruler_subject:wei-yan->zhuge-liang

### 119. `relsem.b8cecfe16d65176f84b68bc4.part004`

- 原文：一天，關羽、張飛不在，劉備正在後園澆菜，許褚、張遼帶著人進到園子裏說：“丞相請你去一趟。
- 候選：ruler_subject:zhang-liao->xu-zhu; ruler_subject:xu-zhu->zhang-liao

### 120. `relsem.b8cecfe16d65176f84b68bc4`

- 原文：一天，關羽、張飛不在，劉備正在後園澆菜，許褚、張遼帶著人進到園子裏說：“丞相請你去一趟。
- 候選：ruler_subject:liu-bei->guan-yu; ruler_subject:guan-yu->liu-bei; ruler_subject:liu-bei->xu-zhu; ruler_subject:xu-zhu->liu-bei; ruler_subject:liu-bei->zhang-fei; ruler_subject:zhang-fei->liu-bei
