li = [23,44,232,7443,12334,8966,23455,8445,24353]
# print(li[:],"\n",li[::],"\n",li[::3],"\n",li[:3])
# li_copy = li[:]
# print(li_copy)

sorted_li1 = sorted(li)
# sorted_li2 = li.sort(reverse=False)
# print(sorted_li1,"\n",sorted_li2)

# for le in li:
#     print(le)

# for i, le in enumerate(li):
#     if i%2 == 0:
#         print(f'{i}')
#     print(f"{i} : {le}")

# matrix = [[22,23,24],[25,26,27],[28,29,30]]

# com = 34+67j
# print(com.imag,com.real)
import array;

li_array = array.array('i',li)
print(li_array)